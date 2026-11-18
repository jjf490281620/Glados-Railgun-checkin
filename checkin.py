"""Run independent GLaDOS / Railgun check-ins and report real failures to CI."""

import html
import hashlib
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


class CheckinStatus(Enum):
    SUCCESS = 0
    REPEAT = 1
    REQUIRES_BROWSER = 2
    FAILURE = -2


@dataclass(frozen=True)
class Account:
    name: str
    cookie: str = field(repr=False)
    domains: tuple[str, ...] = DOMAINS
    email: str = ""


def parse_accounts(raw: str, source: str) -> list[Account]:
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
        if set(entry) - {"name", "email", "cookie", "domain"}:
            raise ValueError(f"账号 {index} 仅支持 name、email、cookie、domain 字段。")

        cookie = entry.get("cookie")
        if not isinstance(cookie, str) or not cookie.strip():
            raise ValueError(f"账号 {index} 缺少有效的 cookie。")
        cookie = re.sub(r"^cookie\s*:\s*", "", cookie.strip(), flags=re.IGNORECASE)
        if any(ord(char) < 32 or ord(char) == 127 for char in cookie) or "=" not in cookie:
            raise ValueError(f"账号 {index} 的 Cookie 格式无效；请复制完整 Cookie 请求头的值。")

        email = entry.get("email", "")
        if not isinstance(email, str) or (email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip())):
            raise ValueError(f"账号 {index} 的 email 必须是有效的邮箱地址。")
        email = email.strip()
        name = entry.get("name", email or f"账号 {index}")
        if not isinstance(name, str) or not name.strip() or len(name) > 60 or any(ord(char) < 32 for char in name):
            raise ValueError(f"账号 {index} 的 name 必须是 1–60 字符的单行名称。")

        domain = entry.get("domain", "auto")
        if not isinstance(domain, str):
            raise ValueError(f"账号 {index} 的 domain 必须是 auto、glados.cloud 或 railgun.info。")
        domain = domain.strip().lower().removeprefix("https://").rstrip("/")
        if domain not in ("auto", *DOMAINS):
            raise ValueError(f"账号 {index} 的 domain 必须是 auto、glados.cloud 或 railgun.info。")
        domains = DOMAINS if domain == "auto" else (domain,)

        key = (cookie, domains)
        if key in seen:
            logger.warning("账号 %s 与前面的 Cookie 和域名相同，跳过重复配置。", index)
            continue
        seen.add(key)
        accounts.append(Account(name.strip(), cookie, domains, email))
    return accounts


class Config:
    def __init__(self):
        self.push_key = os.environ.get("PUSHDEER_SENDKEY", "").strip()
        self.verbose = os.environ.get("GLADOS_VERBOSE", "").strip().lower() in ("true", "1", "yes", "y")
        raw = os.environ.get("GLADOS_ACCOUNTS", "").strip()
        source = "GLADOS_ACCOUNTS"
        if not raw:
            raw = os.environ.get("GLADOS_COOKIES", "").strip()
            source = "GLADOS_COOKIES"
        if not raw:
            raise ValueError("未配置账号：请在 GitHub Actions Secrets 中设置 GLADOS_ACCOUNTS 或 GLADOS_COOKIES。")

        self.accounts = parse_accounts(raw, source)
        register_secret(self.push_key)
        for account in self.accounts:
            register_secret(account.cookie)
            for part in account.cookie.split(";"):
                key, separator, value = part.strip().partition("=")
                if separator and value and (key.lower().startswith(("gld:", "koa:")) or "sess" in key.lower() or "token" in key.lower()):
                    register_secret(value)
        logger.info("已从 %s 加载 %s 个账号。", source, len(self.accounts))


class APIError(Exception):
    """A safe diagnostic that never contains request headers or response bodies."""

    def __init__(self, message: str, http_status=None, browser_required=False):
        super().__init__(message)
        self.http_status = http_status
        self.browser_required = browser_required


@dataclass(frozen=True)
class UserStatus:
    email: str
    days: str


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
                    method, f"https://{self.domain}{path}", json=data,
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
                raise APIError(f"接口返回 HTTP {response.status_code}；请检查域名、Cookie 或稍后重试。", http_status=response.status_code)
            try:
                payload = response.json()
            except ValueError:
                raise APIError("接口返回非 JSON 内容，可能是网站维护或访问验证。") from None
            if not isinstance(payload, dict) or "code" not in payload:
                raise APIError("接口返回了无法识别的数据。")
            return payload
        raise APIError("网络请求失败。")

    def _get_checkin_nonce(self) -> Optional[str]:
        try:
            challenge = self._request("/api/user/checkin/challenge")
        except APIError as error:
            if error.http_status == 404:
                return None
            raise
        if challenge.get("code") != 0:
            raise APIError(display_text(challenge.get("message"), "无法获取签到校验请求。"))
        if challenge.get("turnstile"):
            raise APIError(
                "网站要求完成 Cloudflare Turnstile 网页验证；当前 GitHub 接口任务无法完成本次签到。请在官网登录并检查签到页。",
                browser_required=True,
            )
        nonce = challenge.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise APIError("签到接口未返回有效的校验请求。")
        ttl = number(challenge.get("ttl"))
        if ttl is not None and ttl <= 2500:
            raise APIError("签到校验请求有效期过短，请稍后重试。")
        register_secret(nonce)
        return nonce

    def checkin(self) -> dict:
        nonce = self._get_checkin_nonce()
        payload = {"token": self.domain}
        if nonce:
            # Follow the web client's protocol when browser verification is
            # not required. Never synthesize a Turnstile token.
            time.sleep(2.5)
            payload.update({
                "nonce": nonce,
                "proof": hashlib.sha256((nonce + ".glados-observe-v1").encode("utf-8")).hexdigest(),
                "tsState": "off",
            })
        # A nonce is single-use; a later run obtains a fresh one if the response
        # is lost. Older endpoints without challenges retain bounded retries.
        return self._request("/api/user/checkin", "POST", payload, retry=nonce is None)

    def get_status(self) -> UserStatus:
        payload = self._request("/api/user/status")
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict):
            message = display_text(payload.get("message"), "无法读取账号状态")
            raise APIError(f"{message}（Cookie 未通过认证，请重新登录后更新 Secret）")
        email = data.get("email")
        if not isinstance(email, str) or "@" not in email:
            raise APIError("接口未返回有效邮箱，无法核对 Cookie 对应的账号。")
        days = number(data.get("leftDays"))
        return UserStatus(email.strip(), f"{days} 天" if days is not None else "未知")

    def get_points(self) -> tuple[str, Optional[int]]:
        payload = self._request("/api/user/points")
        points = number(payload.get("points"))
        if payload.get("code") != 0 or points is None:
            raise APIError("无法查询总积分。")
        return str(points), points

@dataclass
class CheckinResult:
    account: str
    domain: str
    email: str = ""
    identity_verified: bool = False
    code: CheckinStatus = CheckinStatus.FAILURE
    points: str = "0"
    days: str = "未知"
    points_total: str = "未知"
    message: str = ""

    @property
    def identifier(self) -> str:
        if self.email and self.email != self.account:
            return f"{self.account} ({self.email})"
        return self.account

    @property
    def ok(self) -> bool:
        return self.code in (CheckinStatus.SUCCESS, CheckinStatus.REPEAT)

    @property
    def status(self) -> str:
        return {
            CheckinStatus.SUCCESS: "✅ 签到成功",
            CheckinStatus.REPEAT: "🔄 今日已签到",
            CheckinStatus.REQUIRES_BROWSER: "⚠️ 需要网页验证",
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
                    result = CheckinResult(account.name, domain, email=account.email, message=message)
                if result.ok or result.identity_verified:
                    break
                failures.append(f"{domain}：{result.message}")
                logger.warning("[%s][%s] %s", account.name, domain, result.message)

            if not result.ok and not result.identity_verified:
                result.domain = " / ".join(account.domains)
                result.message = "；".join(failures)
            self.results.append(result)
            logger.info("[%s][%s] %s，剩余 %s，总积分 %s。", result.identifier, result.domain, result.status, result.days, result.points_total)
        return self.results

    def _checkin_on_domain(self, account: Account, domain: str) -> CheckinResult:
        result = CheckinResult(account.name, domain, email=account.email)
        with API(account, domain, self.config.verbose) as api:
            # Verify the identity before submitting a check-in for this Cookie.
            status = api.get_status()
            if account.email and status.email.casefold() != account.email.casefold():
                result.message = "Cookie 对应的邮箱与配置邮箱不一致，请更新该账号的 Cookie 或 email。"
                return result
            result.email = status.email
            result.days = status.days
            result.identity_verified = True
            try:
                result.points_total, _ = api.get_points()
            except APIError as error:
                logger.warning("[%s][%s] %s", result.identifier, domain, error)
            try:
                payload = api.checkin()
            except APIError as error:
                result.message = str(error)
                if error.browser_required:
                    result.code = CheckinStatus.REQUIRES_BROWSER
                return result
            code = payload.get("code")
            result.message = display_text(payload.get("message"), "接口未返回消息")
            if isinstance(code, bool) or code not in (0, 1):
                if payload.get("reason") in ("turnstile", "device-mismatch"):
                    result.code = CheckinStatus.REQUIRES_BROWSER
                    result.message += "（网站要求在登录对应账号的浏览器中完成验证）"
                if code == -2 or "permission" in result.message.lower() or "权限" in result.message:
                    result.message += "（Cookie 未通过认证，请重新登录后更新 Secret）"
                return result

            result.code = CheckinStatus.REPEAT if code == 1 or payload.get("repeated") is True else CheckinStatus.SUCCESS
            result.points = display_text(payload.get("points", 0), "0") if result.code == CheckinStatus.SUCCESS else "0"
            if self.config.verbose:
                logger.info("[%s][%s] 接口消息：%s", result.identifier, domain, result.message)

            if result.code == CheckinStatus.SUCCESS:
                try:
                    updated_status = api.get_status()
                    if updated_status.email.casefold() != result.email.casefold():
                        result.code = CheckinStatus.FAILURE
                        result.message = "签到后的邮箱与签到前不一致，请检查账号配置。"
                        return result
                    result.days = updated_status.days
                except APIError as error:
                    logger.warning("[%s][%s] 签到后状态查询失败：%s", result.identifier, domain, error)
                try:
                    result.points_total, _ = api.get_points()
                except APIError as error:
                    logger.warning("[%s][%s] %s", result.identifier, domain, error)
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
            f"{result.identifier} [{result.domain}] {result.status} | 剩余 {result.days} | 总积分 {result.points_total}"
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
            "| 账号 | 邮箱 | 域名 | 结果 | 本次积分 | 剩余天数 | 总积分 | 说明 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ])
        for result in results:
            values = (result.account, result.email or "未验证", result.domain, result.status, result.points, result.days, result.points_total, result.message)
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
