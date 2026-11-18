"""Run independent GLaDOS / Railgun check-ins and report real failures to CI."""

import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import requests

from logging_config import init_logger, redact_text, register_secret


logger = init_logger()
DOMAINS = ("glados.cloud", "railgun.info")
EXCHANGE_PLANS = {"plan100": 100, "plan200": 200, "plan500": 500}


class CheckinStatus(Enum):
    SUCCESS = 0
    REPEAT = 1
    FAILURE = -2


@dataclass(frozen=True)
class Account:
    name: str
    cookie: str = field(repr=False)
    domains: tuple[str, ...] = DOMAINS
    exchange_plan: str = "none"


def parse_accounts(raw: str, source: str, exchange_plan: str) -> list[Account]:
    """Accept JSON accounts, one cookie per line, or the legacy '&' format."""
    if source == "GLADOS_ACCOUNTS" or raw.lstrip().startswith("["):
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError(f"{source} 必须是有效的 JSON 数组，请检查引号和逗号。") from None
        if not isinstance(entries, list):
            raise ValueError(f"{source} 必须是 JSON 数组。")
    else:
        entries = [cookie.strip() for line in raw.splitlines() for cookie in line.split("&") if cookie.strip()]

    if not entries:
        raise ValueError(f"{source} 未包含任何账号。")

    accounts = []
    seen = set()
    for index, entry in enumerate(entries, 1):
        if isinstance(entry, str):
            entry = {"cookie": entry}
        if not isinstance(entry, dict):
            raise ValueError(f"账号 {index} 必须是 Cookie 字符串或账号对象。")
        if set(entry) - {"name", "cookie", "domain", "exchange_plan"}:
            raise ValueError(f"账号 {index} 仅支持 name、cookie、domain、exchange_plan 字段。")

        cookie = entry.get("cookie")
        if not isinstance(cookie, str) or not cookie.strip():
            raise ValueError(f"账号 {index} 缺少有效的 cookie。")
        cookie = re.sub(r"^cookie\s*:\s*", "", cookie.strip(), flags=re.IGNORECASE)
        if any(ord(char) < 32 or ord(char) == 127 for char in cookie) or "=" not in cookie:
            raise ValueError(f"账号 {index} 的 Cookie 格式无效；请复制完整 Cookie 请求头的值。")

        name = entry.get("name", f"账号 {index}")
        if not isinstance(name, str) or not name.strip() or len(name) > 60 or any(ord(char) < 32 for char in name):
            raise ValueError(f"账号 {index} 的 name 必须是 1–60 字符的单行名称。")

        domain = entry.get("domain", "auto")
        if not isinstance(domain, str):
            raise ValueError(f"账号 {index} 的 domain 必须是 auto、glados.cloud 或 railgun.info。")
        domain = domain.strip().lower().removeprefix("https://").rstrip("/")
        if domain not in ("auto", *DOMAINS):
            raise ValueError(f"账号 {index} 的 domain 必须是 auto、glados.cloud 或 railgun.info。")
        domains = DOMAINS if domain == "auto" else (domain,)

        plan = entry.get("exchange_plan", exchange_plan)
        if not isinstance(plan, str) or plan not in ("none", *EXCHANGE_PLANS):
            raise ValueError(f"账号 {index} 的 exchange_plan 必须是 none、plan100、plan200 或 plan500。")

        key = (cookie, domains)
        if key in seen:
            logger.warning("账号 %s 与前面的 Cookie 和域名相同，跳过重复配置。", index)
            continue
        seen.add(key)
        accounts.append(Account(name.strip(), cookie, domains, plan))
    return accounts


class Config:
    def __init__(self):
        self.push_key = os.environ.get("PUSHDEER_SENDKEY", "").strip()
        self.verbose = os.environ.get("GLADOS_VERBOSE", "").strip().lower() in ("true", "1", "yes", "y")
        plan = os.environ.get("GLADOS_EXCHANGE_PLAN", "").strip() or "none"
        if plan not in ("none", *EXCHANGE_PLANS):
            logger.warning("GLADOS_EXCHANGE_PLAN 无效，已关闭自动兑换。")
            plan = "none"

        raw = os.environ.get("GLADOS_ACCOUNTS", "").strip()
        source = "GLADOS_ACCOUNTS"
        if not raw:
            raw = os.environ.get("GLADOS_COOKIES", "").strip()
            source = "GLADOS_COOKIES"
        if not raw:
            raise ValueError("未配置账号：请在 GitHub Actions Secrets 中设置 GLADOS_ACCOUNTS 或 GLADOS_COOKIES。")

        self.accounts = parse_accounts(raw, source, plan)
        register_secret(self.push_key)
        for account in self.accounts:
            register_secret(account.cookie)
            for part in account.cookie.split(";"):
                key, separator, value = part.strip().partition("=")
                if separator and value and ("sess" in key.lower() or "token" in key.lower()):
                    register_secret(value)
        logger.info("已从 %s 加载 %s 个账号。", source, len(self.accounts))


class APIError(Exception):
    """A safe diagnostic that never contains request headers or response bodies."""


def display_text(value, fallback="未知") -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return fallback
    return str(value).replace("\r", " ").replace("\n", " ")[:500]


def number(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


class API:
    def __init__(self, account: Account, domain: str, verbose: bool = False):
        self.account = account
        self.domain = domain
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            "Cookie": account.cookie,
            "Origin": f"https://{domain}",
            "Referer": f"https://{domain}/console/checkin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        })

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.session.close()

    def _request(self, path: str, method="GET", data=None, retry=True) -> dict:
        attempts = 3 if retry else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method, f"https://{self.domain}{path}", data=data,
                    timeout=(10, 30), allow_redirects=False,
                )
            except requests.RequestException as error:
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise APIError(f"网络请求失败（{type(error).__name__}）") from None

            if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                time.sleep(2 ** attempt)
                continue
            if not 200 <= response.status_code < 300:
                raise APIError(f"接口返回 HTTP {response.status_code}；请检查域名、Cookie 或稍后重试。")
            try:
                payload = response.json()
            except ValueError:
                raise APIError("接口返回非 JSON 内容，可能是网站维护或访问验证。") from None
            if not isinstance(payload, dict) or "code" not in payload:
                raise APIError("接口返回了无法识别的数据。")
            return payload
        raise APIError("网络请求失败。")

    def checkin(self) -> dict:
        # Check-in is idempotent: a retried successful request returns code 1.
        return self._request("/api/user/checkin", "POST", {"token": self.domain})

    def get_status(self) -> str:
        payload = self._request("/api/user/status")
        data = payload.get("data")
        days = number(data.get("leftDays")) if isinstance(data, dict) else None
        if payload.get("code") != 0 or days is None:
            raise APIError("无法查询剩余天数。")
        return f"{days} 天"

    def get_points(self) -> tuple[str, Optional[int]]:
        payload = self._request("/api/user/points")
        points = number(payload.get("points"))
        if payload.get("code") != 0 or points is None:
            raise APIError("无法查询总积分。")
        return str(points), points

    def exchange(self, plan: str) -> str:
        # Never retry an exchange: a timeout may happen after points are spent.
        payload = self._request("/api/user/exchange", "POST", {"planType": plan}, retry=False)
        if payload.get("code") == 0:
            return f"兑换成功：{plan}"
        return f"兑换失败：{display_text(payload.get('message'))}"


@dataclass
class CheckinResult:
    account: str
    domain: str
    code: CheckinStatus = CheckinStatus.FAILURE
    points: str = "0"
    days: str = "未知"
    points_total: str = "未知"
    message: str = ""
    exchange: str = "未启用"

    @property
    def ok(self) -> bool:
        return self.code in (CheckinStatus.SUCCESS, CheckinStatus.REPEAT)

    @property
    def status(self) -> str:
        return {
            CheckinStatus.SUCCESS: "✅ 签到成功",
            CheckinStatus.REPEAT: "🔄 今日已签到",
            CheckinStatus.FAILURE: "❌ 签到失败",
        }[self.code]


class Checker:
    def __init__(self, config: Config):
        self.config = config
        self.results: list[CheckinResult] = []

    def checkin_all(self) -> list[CheckinResult]:
        self.results = []
        for index, account in enumerate(self.config.accounts, 1):
            logger.info("[%s/%s] 开始处理 %s。", index, len(self.config.accounts), account.name)
            failures = []
            for domain in account.domains:
                try:
                    result = self._checkin_on_domain(account, domain)
                except Exception as error:
                    # One account must not prevent the others from being checked in.
                    message = str(error) if isinstance(error, APIError) else f"账号处理异常（{type(error).__name__}）"
                    result = CheckinResult(account.name, domain, message=message)
                if result.ok:
                    break
                failures.append(f"{domain}：{result.message}")
                logger.warning("[%s][%s] %s", account.name, domain, result.message)

            if not result.ok:
                result.domain = " / ".join(account.domains)
                result.message = "；".join(failures)
            self.results.append(result)
            logger.info("[%s][%s] %s，剩余 %s，总积分 %s。", account.name, result.domain, result.status, result.days, result.points_total)
        return self.results

    def _checkin_on_domain(self, account: Account, domain: str) -> CheckinResult:
        result = CheckinResult(account.name, domain)
        with API(account, domain, self.config.verbose) as api:
            payload = api.checkin()
            code = payload.get("code")
            result.message = display_text(payload.get("message"), "接口未返回消息")
            if isinstance(code, bool) or code not in (0, 1):
                if code == -2 or "permission" in result.message.lower() or "权限" in result.message:
                    result.message += "（Cookie 未通过认证，请重新登录后更新 Secret）"
                return result

            result.code = CheckinStatus(code)
            result.points = display_text(payload.get("points", 0), "0") if code == 0 else "0"
            if self.config.verbose:
                logger.info("[%s][%s] 接口消息：%s", account.name, domain, result.message)

            try:
                result.days = api.get_status()
            except APIError as error:
                logger.warning("[%s][%s] %s", account.name, domain, error)
            points = None
            try:
                result.points_total, points = api.get_points()
            except APIError as error:
                logger.warning("[%s][%s] %s", account.name, domain, error)

            if account.exchange_plan in EXCHANGE_PLANS:
                required = EXCHANGE_PLANS[account.exchange_plan]
                if result.code == CheckinStatus.REPEAT:
                    result.exchange = "今日已签到，跳过兑换"
                elif points is None or points < required:
                    result.exchange = f"积分不足或无法查询，跳过兑换（需要 {required}）"
                else:
                    try:
                        result.exchange = api.exchange(account.exchange_plan)
                        # Show the current balance after a successful exchange.
                        if result.exchange.startswith("兑换成功"):
                            result.days = api.get_status()
                            result.points_total, _ = api.get_points()
                    except APIError as error:
                        result.exchange = f"兑换或兑换后查询失败：{error}"
                    logger.info("[%s][%s] %s", account.name, domain, result.exchange)
        return result

    @property
    def has_failures(self) -> bool:
        return not self.results or any(not result.ok for result in self.results)

    def format_results(self) -> tuple[str, str]:
        successes = sum(result.code == CheckinStatus.SUCCESS for result in self.results)
        repeats = sum(result.code == CheckinStatus.REPEAT for result in self.results)
        failures = sum(not result.ok for result in self.results)
        title = f"GLaDOS 签到：成功 {successes}，已签到 {repeats}，失败 {failures}"
        content = "\n".join(
            f"{result.account} [{result.domain}] {result.status} | 剩余 {result.days} | 总积分 {result.points_total} | {result.exchange}"
            + (f" | {result.message}" if not result.ok else "")
            for result in self.results
        )
        return redact_text(title), redact_text(content)


def markdown_cell(value) -> str:
    text = html.escape(redact_text(str(value))).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_[\]{}()!|])", r"\\\1", text)


def write_summary(title: str, results: list[CheckinResult], error="") -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = [f"## {markdown_cell(title)}", ""]
    if results:
        lines.extend([
            "| 账号 | 域名 | 结果 | 本次积分 | 剩余天数 | 总积分 | 兑换 | 说明 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ])
        for result in results:
            values = (result.account, result.domain, result.status, result.points, result.days, result.points_total, result.exchange, result.message)
            lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")
    if error:
        lines.append(markdown_cell(error))
    lines.extend(["", "今日已签到视为成功。失败账号不会阻止其他账号签到；任一账号失败会使签到任务失败。", ""])
    with Path(target).open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines))


class PushService:
    def __init__(self, config: Config):
        self.config = config

    def send(self, title: str, content: str) -> None:
        if not self.config.push_key:
            return
        try:
            from pypushdeer import PushDeer

            PushDeer(pushkey=self.config.push_key).send_text(title, desp=content)
            logger.info("推送通知发送成功。")
        except Exception as error:
            logger.warning("推送通知失败（%s），请检查 PUSHDEER_SENDKEY。", type(error).__name__)


def main() -> int:
    try:
        config = Config()
    except ValueError as error:
        logger.error("配置错误：%s", error)
        write_summary("GLaDOS 配置错误", [], str(error))
        return 2

    checker = Checker(config)
    checker.checkin_all()
    title, content = checker.format_results()
    logger.info("%s\n%s", title, content)
    write_summary(title, checker.results)
    PushService(config).send(title, content)
    return 1 if checker.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
