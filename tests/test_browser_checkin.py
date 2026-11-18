import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from browser_checkin import BrowserCheckinError, browser_cookies, checkin_in_browser
from checkin import Checker, CheckinStatus, Config, UserStatus, PLATFORM_USER_AGENTS


COOKIE = "gld:sess=example-token; gld:sess.sig=example-signature; koa:sess=value==;"


class BrowserFallbackTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "GLADOS_ACCOUNTS": json.dumps([{"cookie": COOKIE, "email": "one@example.com", "domain": "glados.cloud"}]),
            "GLADOS_BROWSER_FALLBACK": "true",
            "GLADOS_BROWSER_HEADLESS": "false",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.checker = Checker(Config())

    def api(self, client, payload):
        api = client.return_value.__enter__.return_value
        api.get_status.return_value = UserStatus("one@example.com", "3 天")
        api.get_points.return_value = ("99", 99)
        api.session.headers = {"User-Agent": PLATFORM_USER_AGENTS["macos"]}
        api.checkin.return_value = payload
        return api

    def test_turnstile_rejection_uses_verified_email_and_recovered_browser_agent(self):
        with patch("checkin.API") as client, patch("browser_checkin.checkin_in_browser", return_value={"code": 0, "points": 3}) as browser:
            api = self.api(client, {"code": 5, "reason": "turnstile", "message": "Cloudflare verification failed"})
            result = self.checker._checkin_on_domain(self.checker.config.accounts[0], "glados.cloud")
            browser.assert_called_once_with(COOKIE, "glados.cloud", "one@example.com", PLATFORM_USER_AGENTS["macos"], False)
            self.assertEqual(api.get_status.call_count, 2)
        self.assertEqual(result.code, CheckinStatus.SUCCESS)
        self.assertEqual((result.method, result.points, result.email), ("网页", "3", "one@example.com"))

    def test_success_and_cookie_failures_do_not_launch_browser(self):
        for payload in ({"code": 1, "repeated": True}, {"code": -2, "message": "No permission"}):
            with self.subTest(payload=payload), patch("checkin.API") as client, patch("browser_checkin.checkin_in_browser") as browser:
                self.api(client, payload)
                result = self.checker._checkin_on_domain(self.checker.config.accounts[0], "glados.cloud")
                browser.assert_not_called()
                self.assertEqual(result.method, "接口")

    def test_disabled_fallback_reports_real_verification_failure(self):
        self.checker.config.browser_fallback = False
        with patch("checkin.API") as client, patch("browser_checkin.checkin_in_browser") as browser:
            self.api(client, {"code": 5, "reason": "turnstile"})
            result = self.checker._checkin_on_domain(self.checker.config.accounts[0], "glados.cloud")
            browser.assert_not_called()
        self.assertEqual(result.code, CheckinStatus.REQUIRES_BROWSER)

    def test_browser_rejection_is_not_a_success(self):
        with patch("checkin.API") as client, patch("browser_checkin.checkin_in_browser", return_value={"code": 5, "reason": "turnstile", "message": "Cloudflare verification failed"}):
            self.api(client, {"code": 5, "reason": "turnstile"})
            result = self.checker._checkin_on_domain(self.checker.config.accounts[0], "glados.cloud")
        self.assertEqual(result.code, CheckinStatus.REQUIRES_BROWSER)
        self.assertFalse(result.ok)
        self.assertEqual((result.email, result.days, result.points_total), ("one@example.com", "3 天", "99"))
        self.assertIn("仍未通过", result.message)

    def test_browser_failure_does_not_block_later_accounts(self):
        os.environ["GLADOS_ACCOUNTS"] = json.dumps([
            {"cookie": COOKIE, "email": "one@example.com", "domain": "glados.cloud"},
            {"cookie": COOKIE.replace("example-token", "second-token"), "email": "two@example.com", "domain": "glados.cloud"},
        ])
        checker = Checker(Config())
        with patch("checkin.API") as client, patch("browser_checkin.checkin_in_browser", side_effect=[BrowserCheckinError("需要人工验证", browser_required=True), {"code": 0, "points": 5}]):
            api = self.api(client, {"code": 5, "reason": "turnstile"})
            api.get_status.side_effect = [UserStatus("one@example.com", "3 天"), UserStatus("two@example.com", "3 天"), UserStatus("two@example.com", "3 天")]
            results = checker.checkin_all()
        self.assertEqual([result.code for result in results], [CheckinStatus.REQUIRES_BROWSER, CheckinStatus.SUCCESS])
        self.assertEqual([result.email for result in results], ["one@example.com", "two@example.com"])
        self.assertTrue(checker.has_failures)


class BrowserTransportTests(unittest.TestCase):
    def runtime(self, payload=None, email="one@example.com"):
        runtime = Mock()
        browser = runtime.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = "https://glados.cloud/console/checkin"
        context.request.get.return_value = Mock(ok=True, json=Mock(return_value={"code": 0, "data": {"email": email}}))
        submission = Mock()
        submission.value = Mock(ok=True, json=Mock(return_value=payload or {"code": 0, "points": 5}))
        page.expect_response.return_value.__enter__ = Mock(return_value=submission)
        page.expect_response.return_value.__exit__ = Mock(return_value=False)
        manager = Mock()
        manager.__enter__ = Mock(return_value=runtime)
        manager.__exit__ = Mock(return_value=False)
        return runtime, browser, context, page, manager

    def test_cookie_values_remain_exact_and_are_scoped_to_account_domain(self):
        cookies = browser_cookies(COOKIE, "glados.cloud")
        self.assertEqual([cookie["value"] for cookie in cookies], ["example-token", "example-signature", "value=="])
        self.assertTrue(all(cookie["url"] == "https://glados.cloud/" for cookie in cookies))
        self.assertTrue(all(cookie["secure"] and cookie["httpOnly"] for cookie in cookies))
        with self.assertRaises(BrowserCheckinError):
            browser_cookies(COOKIE, "attacker.example")

    def test_official_page_submission_is_returned_and_session_is_closed(self):
        runtime, browser, context, page, manager = self.runtime()
        with patch("playwright.sync_api.sync_playwright", return_value=manager):
            result = checkin_in_browser(COOKIE, "glados.cloud", "one@example.com", "Browser/1.0", False)
        runtime.chromium.launch.assert_called_once_with(headless=False)
        context.add_cookies.assert_called_once_with(browser_cookies(COOKIE, "glados.cloud"))
        page.get_by_role.return_value.click.assert_called_once()
        self.assertEqual(result, {"code": 0, "points": 5})
        context.close.assert_called_once()
        browser.close.assert_called_once()
        patterns = [call.args[0] for call in context.route.call_args_list]
        self.assertIn("https://glados.cloud/api/user/exchange**", patterns)
        self.assertIn("https://glados.cloud/api/user/stake**", patterns)
        matcher = page.expect_response.call_args.args[0]
        self.assertTrue(matcher(SimpleNamespace(url="https://glados.cloud/api/user/checkin", request=SimpleNamespace(method="POST"))))
        self.assertFalse(matcher(SimpleNamespace(url="https://attacker.example/api/user/checkin", request=SimpleNamespace(method="POST"))))
        self.assertFalse(matcher(SimpleNamespace(url="https://glados.cloud/api/user/checkin/challenge", request=SimpleNamespace(method="GET"))))

    def test_email_mismatch_stops_before_clicking_and_closes_context(self):
        _, browser, context, page, manager = self.runtime(email="different@example.com")
        with patch("playwright.sync_api.sync_playwright", return_value=manager), self.assertRaisesRegex(BrowserCheckinError, "邮箱不一致"):
            checkin_in_browser(COOKIE, "glados.cloud", "one@example.com", "Browser/1.0")
        page.get_by_role.assert_not_called()
        context.close.assert_called_once()
        browser.close.assert_called_once()

    def test_response_timeout_remains_explicit_verification_failure(self):
        from playwright.sync_api import TimeoutError

        _, browser, context, page, manager = self.runtime()
        page.expect_response.return_value.__enter__.side_effect = TimeoutError(COOKIE)
        with patch("playwright.sync_api.sync_playwright", return_value=manager), self.assertRaises(BrowserCheckinError) as raised:
            checkin_in_browser(COOKIE, "glados.cloud", "one@example.com", "Browser/1.0")
        self.assertTrue(raised.exception.browser_required)
        self.assertNotIn(COOKIE, str(raised.exception))
        context.close.assert_called_once()
        browser.close.assert_called_once()

    def test_read_only_probe_observes_widget_without_clicking_or_logging_token(self):
        _, browser, context, page, manager = self.runtime()
        page.locator.return_value.count.return_value = 1
        page.locator.return_value.first.input_value.return_value = "official-widget-token"
        with patch("playwright.sync_api.sync_playwright", return_value=manager):
            outcome = checkin_in_browser(COOKIE, "glados.cloud", "one@example.com", "Browser/1.0", submit=False)
        self.assertTrue(outcome["email_verified"])
        self.assertTrue(outcome["verification_ready"])
        self.assertNotIn("official-widget-token", repr(outcome))
        page.get_by_role.return_value.click.assert_not_called()
        page.expect_response.assert_not_called()
        context.close.assert_called_once()
        browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
