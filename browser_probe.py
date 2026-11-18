"""Read-only validation of isolated account sessions and official verification."""

import argparse
import os
from pathlib import Path

from browser_checkin import BrowserCheckinError, checkin_in_browser
from checkin import API, Config, PLATFORM_USER_AGENTS, logger, markdown_cell


def main() -> int:
    parser = argparse.ArgumentParser(description="检查官网浏览器登录及验证组件，不提交签到或兑换。")
    parser.add_argument("--platform", choices=tuple(PLATFORM_USER_AGENTS), default="macos")
    args = parser.parse_args()
    config = Config()
    rows = []
    failed = False
    for account in config.accounts:
        domain = account.domains[0]
        logger.info("[%s] 检查独立浏览器会话及官网验证组件。", account.name)
        try:
            with API(account, domain) as api:
                status = api.get_status()
                if account.email and account.email.casefold() != status.email.casefold():
                    raise BrowserCheckinError("Cookie 与配置邮箱不一致。")
            outcome = checkin_in_browser(
                account.cookie, domain, status.email,
                account.user_agent or PLATFORM_USER_AGENTS[args.platform],
                headless=config.browser_headless, submit=False,
            )
            ready = outcome["verification_ready"]
            failed = failed or not ready
            message = "官网验证组件已完成验证" if ready else "官网验证组件尚未完成验证"
            rows.append((status.email, "已核验", message))
            logger.info("[%s] %s。", status.email, message)
        except Exception as error:
            failed = True
            message = str(error) if isinstance(error, BrowserCheckinError) else f"检查失败（{type(error).__name__}）"
            rows.append((account.email or account.name, "检查失败", message))
            logger.warning("[%s] %s。", account.name, message)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["## 官网网页签到环境检查", "", "本检查只读取登录状态和官网验证组件，不提交签到或兑换。", "", "| 账号邮箱 | 登录会话 | 官网验证组件 |", "| --- | --- | --- |"]
        lines.extend("| " + " | ".join(markdown_cell(cell) for cell in row) + " |" for row in rows)
        with Path(summary).open("a", encoding="utf-8") as target:
            target.write("\n".join(lines) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
