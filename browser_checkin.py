"""Submit through the official check-in page in an isolated browser session."""

import re
import time
from urllib.parse import urlsplit


class BrowserCheckinError(Exception):
    def __init__(self, message: str, browser_required=False):
        super().__init__(message)
        self.browser_required = browser_required


def browser_cookies(cookie: str, domain: str) -> list[dict]:
    if domain not in ("glados.cloud", "railgun.info"):
        raise BrowserCheckinError("网页签到域名不在允许列表中。")
    cookies = []
    for part in cookie.split(";"):
        name, separator, value = part.strip().partition("=")
        if name and separator:
            cookies.append({
                "name": name,
                "value": value,
                "url": f"https://{domain}/",
                "secure": True,
                "httpOnly": name.startswith(("gld:", "koa:")),
                "sameSite": "Lax",
            })
    return cookies


def checkin_in_browser(cookie: str, domain: str, expected_email: str, user_agent: str, headless=True, submit=True) -> dict:
    """Let the website render its own verification and submit its own request.

    No CAPTCHA solver, token synthesis, browser stealth, or browser profile is
    used. A challenge requiring human input remains an explicit failure.
    """
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError:
        raise BrowserCheckinError("缺少网页签到依赖，请安装 requirements.txt 和 Playwright Chromium。") from None

    cookies = browser_cookies(cookie, domain)
    if not expected_email:
        raise BrowserCheckinError("网页签到前必须先核验账号邮箱。")
    origin = f"https://{domain}"

    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=headless)
            try:
                context = browser.new_context(user_agent=user_agent, locale="en-US", timezone_id="Asia/Shanghai")
                try:
                    context.add_cookies(cookies)
                    # The task never exchanges or stakes points, even if the
                    # official page adds automatic behavior in the future.
                    context.route(f"{origin}/api/user/exchange**", lambda route: route.abort())
                    context.route(f"{origin}/api/user/stake**", lambda route: route.abort())
                    page = context.new_page()
                    page.set_default_timeout(30_000)
                    challenge_state = {"required": None}

                    def observe_challenge(response):
                        target = urlsplit(response.url)
                        if target.hostname != domain or target.path != "/api/user/checkin/challenge":
                            return
                        try:
                            challenge = response.json()
                            if isinstance(challenge, dict) and str(challenge.get("code")) == "0":
                                challenge_state["required"] = bool(challenge.get("turnstile"))
                        except (ValueError, Error):
                            pass

                    page.on("response", observe_challenge)
                    page.goto(f"{origin}/console/checkin", wait_until="domcontentloaded", timeout=45_000)
                    if urlsplit(page.url).hostname != domain:
                        raise BrowserCheckinError("签到页面跳转到其他域名；已停止该账号。")

                    status_response = context.request.get(f"{origin}/api/user/status", timeout=30_000)
                    if not status_response.ok:
                        raise BrowserCheckinError("网页会话未通过认证，请更新该账号的 Cookie。")
                    status = status_response.json()
                    data = status.get("data") if isinstance(status, dict) else None
                    email = data.get("email") if isinstance(data, dict) else None
                    if not isinstance(email, str) or str(status.get("code")) != "0":
                        raise BrowserCheckinError("网页会话无法读取账号邮箱，已停止签到。")
                    if email.casefold() != expected_email.casefold():
                        raise BrowserCheckinError("网页会话邮箱与配置邮箱不一致，已停止签到。")

                    button = page.get_by_role("button", name=re.compile(r"^(?:check\s?in|签到|簽到)$", re.IGNORECASE))
                    button.wait_for(state="visible", timeout=30_000)
                    # Allow the official page to load its challenge and widget.
                    # The page itself manages token generation and submission.
                    page.wait_for_timeout(4_000)

                    if not submit:
                        # Read-only integration check: observe the official
                        # widget's result without clicking or submitting it.
                        ready = False
                        deadline = time.monotonic() + 45
                        while time.monotonic() < deadline:
                            if challenge_state["required"] is False:
                                ready = True
                                break
                            field = page.locator('input[name="cf-turnstile-response"]')
                            if field.count() and field.first.input_value():
                                ready = True
                                break
                            page.wait_for_timeout(1_000)
                        return {"email_verified": True, "turnstile_required": challenge_state["required"], "verification_ready": ready}

                    def is_checkin(response):
                        target = urlsplit(response.url)
                        return target.hostname == domain and target.path == "/api/user/checkin" and response.request.method == "POST"

                    with page.expect_response(is_checkin, timeout=105_000) as submission:
                        button.click()
                    response = submission.value
                    if not response.ok:
                        raise BrowserCheckinError(f"网页签到接口返回 HTTP {response.status}。")
                    payload = response.json()
                    if not isinstance(payload, dict) or "code" not in payload:
                        raise BrowserCheckinError("网页签到接口返回无法识别的数据。")
                    return payload
                finally:
                    context.close()
            finally:
                browser.close()
    except BrowserCheckinError:
        raise
    except TimeoutError:
        raise BrowserCheckinError(
            "网页签到等待超时，可能需要人工完成 Cloudflare 验证；请在正常浏览器中查看该账号的签到页。",
            browser_required=True,
        ) from None
    except Error:
        raise BrowserCheckinError("网页签到浏览器执行失败；请检查 Chromium 安装和运行环境。") from None
    except (ValueError, TypeError):
        raise BrowserCheckinError("网页签到返回内容无法解析。") from None
