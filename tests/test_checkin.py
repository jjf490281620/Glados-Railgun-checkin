import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import checkin
import logging_config
from checkin import API, APIError, Account, Checker, CheckinResult, CheckinStatus, Config, parse_accounts


def cookie(label):
    return f"koa:sess={label}-session-value; koa:sess.sig={label}-signature-value;"


class CheckinTestCase(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(logging_config._secrets.clear)

    def config(self, cookies=None, accounts=None, **values):
        if cookies is not None:
            os.environ["GLADOS_COOKIES"] = cookies
        if accounts is not None:
            os.environ["GLADOS_ACCOUNTS"] = json.dumps(accounts)
        os.environ.update(values)
        return Config()


class ConfigTests(CheckinTestCase):
    def test_multiline_and_legacy_formats(self):
        for separator in ("\n", "\r\n", "&"):
            with self.subTest(separator=separator):
                accounts = parse_accounts(f" Cookie: {cookie('one')}{separator}{cookie('two')} ", "GLADOS_COOKIES", "none")
                self.assertEqual([account.cookie for account in accounts], [cookie("one"), cookie("two")])
                self.assertEqual([account.name for account in accounts], ["账号 1", "账号 2"])

    def test_json_preserves_ampersands_and_account_options(self):
        accounts = parse_accounts(json.dumps([
            {"name": "主账号", "domain": "https://railgun.info/", "cookie": cookie("a&b"), "exchange_plan": "plan100"},
            cookie("two"),
        ]), "GLADOS_ACCOUNTS", "none")
        self.assertEqual(accounts[0].domains, ("railgun.info",))
        self.assertEqual(accounts[0].cookie, cookie("a&b"))
        self.assertEqual(accounts[0].exchange_plan, "plan100")
        self.assertEqual(accounts[1].domains, checkin.DOMAINS)

    def test_preferred_secret_overrides_legacy(self):
        config = self.config(cookies=cookie("old"), accounts=[{"name": "new", "cookie": cookie("new")}])
        self.assertEqual(len(config.accounts), 1)
        self.assertEqual(config.accounts[0].name, "new")

    def test_json_can_be_stored_in_legacy_secret(self):
        config = self.config(cookies=json.dumps([cookie("one"), cookie("two")]))
        self.assertEqual(len(config.accounts), 2)

    def test_duplicate_cookie_and_domain_are_not_run_twice(self):
        accounts = parse_accounts(f"{cookie('same')}\n{cookie('same')}", "GLADOS_COOKIES", "none")
        self.assertEqual(len(accounts), 1)

    def test_invalid_configs_reject_without_echoing_credentials(self):
        invalid = (
            "[]", "{}", "[", json.dumps([{}]),
            json.dumps([{"cookie": cookie("private"), "domain": "attacker.example"}]),
            json.dumps([{"cookie": cookie("private"), "exchange_plan": "plan999"}]),
            json.dumps([{"cookie": cookie("private"), "name": "bad\nname"}]),
            json.dumps([{"cookie": cookie("private") + "\r\nInjected: header"}]),
            json.dumps([{"cookie": cookie("private"), "cookies": "typo"}]),
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError) as raised:
                    parse_accounts(raw, "GLADOS_ACCOUNTS", "none")
                self.assertNotIn(cookie("private"), str(raised.exception))

    def test_global_exchange_default_and_per_account_override(self):
        config = self.config(accounts=[{"cookie": cookie("one")}, {"cookie": cookie("two"), "exchange_plan": "none"}], GLADOS_EXCHANGE_PLAN="plan200")
        self.assertEqual([account.exchange_plan for account in config.accounts], ["plan200", "none"])
        self.assertNotIn(cookie("one"), repr(config.accounts[0]))

    def test_missing_config_exits_nonzero_and_writes_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            self.assertEqual(checkin.main(), 2)
            self.assertIn("未配置账号", summary.read_text())


class AccountExecutionTests(CheckinTestCase):
    def test_failed_account_does_not_block_success_or_repeat(self):
        checker = Checker(self.config(cookies="\n".join(cookie(name) for name in ("one", "two", "three"))))

        def result(account, domain):
            if account.name == "账号 1":
                raise RuntimeError(cookie("one"))
            code = CheckinStatus.SUCCESS if account.name == "账号 2" else CheckinStatus.REPEAT
            return CheckinResult(account.name, domain, code=code)

        with patch.object(checker, "_checkin_on_domain", side_effect=result):
            results = checker.checkin_all()
        self.assertEqual([result.code for result in results], [CheckinStatus.FAILURE, CheckinStatus.SUCCESS, CheckinStatus.REPEAT])
        self.assertTrue(checker.has_failures)
        self.assertNotIn(cookie("one"), checker.format_results()[1])
        self.assertIn("成功 1，已签到 1，失败 1", checker.format_results()[0])

    def test_auto_domain_falls_back_and_keeps_one_result_per_account(self):
        checker = Checker(self.config(cookies=cookie("one")))
        results = [CheckinResult("账号 1", "glados.cloud", message="没有权限"), CheckinResult("账号 1", "railgun.info", code=CheckinStatus.SUCCESS)]
        with patch.object(checker, "_checkin_on_domain", side_effect=results) as run:
            checker.checkin_all()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(checker.results), 1)
        self.assertEqual(checker.results[0].domain, "railgun.info")
        self.assertFalse(checker.has_failures)

    def test_success_on_first_domain_skips_second_domain(self):
        checker = Checker(self.config(cookies=cookie("one")))
        with patch.object(checker, "_checkin_on_domain", return_value=CheckinResult("账号 1", "glados.cloud", code=CheckinStatus.SUCCESS)) as run:
            checker.checkin_all()
        run.assert_called_once()

    def test_explicit_domain_does_not_try_other_domain(self):
        checker = Checker(self.config(accounts=[{"cookie": cookie("one"), "domain": "railgun.info"}]))
        with patch.object(checker, "_checkin_on_domain", return_value=CheckinResult("账号 1", "railgun.info", message="No permission")) as run:
            checker.checkin_all()
        run.assert_called_once_with(checker.config.accounts[0], "railgun.info")

    def test_real_checkin_failure_is_nonzero_and_repeat_is_success(self):
        self.config(cookies=cookie("one"))
        for code, exit_code in ((CheckinStatus.FAILURE, 1), (CheckinStatus.SUCCESS, 0), (CheckinStatus.REPEAT, 0)):
            with self.subTest(code=code):
                with patch.object(Checker, "_checkin_on_domain", return_value=CheckinResult("账号 1", "glados.cloud", code=code)):
                    self.assertEqual(checkin.main(), exit_code)

    def test_checkin_points_and_post_checkin_balance_are_recorded(self):
        checker = Checker(self.config(cookies=cookie("one")))
        with patch("checkin.API") as client:
            api = client.return_value.__enter__.return_value
            api.checkin.return_value = {"code": 0, "points": 3, "message": "ok"}
            api.get_status.return_value = "25 天"
            api.get_points.return_value = ("99", 99)
            result = checker._checkin_on_domain(checker.config.accounts[0], "glados.cloud")
        self.assertEqual((result.points, result.days, result.points_total), ("3", "25 天", "99"))

    def test_failed_checkin_skips_balance_and_exchange(self):
        checker = Checker(self.config(cookies=cookie("one"), GLADOS_EXCHANGE_PLAN="plan100"))
        with patch("checkin.API") as client:
            api = client.return_value.__enter__.return_value
            api.checkin.return_value = {"code": -2, "message": "No permission"}
            result = checker._checkin_on_domain(checker.config.accounts[0], "glados.cloud")
            api.get_status.assert_not_called()
            api.get_points.assert_not_called()
            api.exchange.assert_not_called()
        self.assertFalse(result.ok)
        self.assertIn("更新 Secret", result.message)

    def test_exchange_requires_new_checkin_and_enough_points(self):
        checker = Checker(self.config(cookies=cookie("one"), GLADOS_EXCHANGE_PLAN="plan100"))
        for code, points in ((0, 99), (1, 200)):
            with self.subTest(code=code, points=points):
                with patch("checkin.API") as client:
                    api = client.return_value.__enter__.return_value
                    api.checkin.return_value = {"code": code, "message": "ok"}
                    api.get_status.return_value = "25 天"
                    api.get_points.return_value = (str(points), points)
                    checker._checkin_on_domain(checker.config.accounts[0], "glados.cloud")
                    api.exchange.assert_not_called()

    def test_confirmed_exchange_refreshes_balance(self):
        checker = Checker(self.config(cookies=cookie("one"), GLADOS_EXCHANGE_PLAN="plan100"))
        with patch("checkin.API") as client:
            api = client.return_value.__enter__.return_value
            api.checkin.return_value = {"code": 0, "message": "ok"}
            api.get_status.side_effect = ["25 天", "35 天"]
            api.get_points.side_effect = [("150", 150), ("50", 50)]
            api.exchange.return_value = "兑换成功：plan100"
            result = checker._checkin_on_domain(checker.config.accounts[0], "glados.cloud")
            api.exchange.assert_called_once_with("plan100")
        self.assertEqual((result.days, result.points_total), ("35 天", "50"))

    def test_optional_balance_error_does_not_undo_confirmed_checkin(self):
        checker = Checker(self.config(cookies=cookie("one")))
        with patch("checkin.API") as client:
            api = client.return_value.__enter__.return_value
            api.checkin.return_value = {"code": 0, "message": "ok"}
            api.get_status.side_effect = APIError("无法查询剩余天数")
            api.get_points.side_effect = APIError("无法查询总积分")
            result = checker._checkin_on_domain(checker.config.accounts[0], "glados.cloud")
        self.assertTrue(result.ok)


class TransportTests(CheckinTestCase):
    def response(self, status=200, payload=None):
        return Mock(status_code=status, json=Mock(return_value=payload if payload is not None else {"code": 0}))

    def test_sessions_isolate_cookies_and_do_not_follow_redirects(self):
        with API(Account("one", cookie("one")), "glados.cloud") as first, API(Account("two", cookie("two")), "railgun.info") as second:
            self.assertIsNot(first.session, second.session)
            self.assertEqual(first.session.headers["Cookie"], cookie("one"))
            self.assertEqual(second.session.headers["Cookie"], cookie("two"))
            with patch.object(first.session, "request", return_value=self.response()) as request:
                first.checkin()
            self.assertEqual(request.call_args.kwargs["data"], {"token": "glados.cloud"})
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
            self.assertEqual(request.call_args.kwargs["timeout"], (10, 30))

    def test_transient_server_errors_are_retried(self):
        with API(Account("one", cookie("one")), "glados.cloud") as api:
            with patch.object(api.session, "request", side_effect=[self.response(503), self.response()]) as request, patch("checkin.time.sleep"):
                self.assertEqual(api.checkin()["code"], 0)
                self.assertEqual(request.call_count, 2)

    def test_network_retry_is_bounded_and_does_not_echo_headers(self):
        with API(Account("one", cookie("one")), "glados.cloud") as api:
            with patch.object(api.session, "request", side_effect=requests.Timeout(cookie("one"))) as request, patch("checkin.time.sleep"):
                with self.assertRaises(APIError) as raised:
                    api.checkin()
                self.assertEqual(request.call_count, 3)
                self.assertNotIn(cookie("one"), str(raised.exception))

    def test_exchange_is_never_retried_after_timeout(self):
        with API(Account("one", cookie("one")), "glados.cloud") as api:
            with patch.object(api.session, "request", side_effect=requests.Timeout()) as request:
                with self.assertRaises(APIError):
                    api.exchange("plan100")
                request.assert_called_once()

    def test_non_json_or_unrecognized_response_fails_safely(self):
        for response in (Mock(status_code=200, json=Mock(side_effect=ValueError("private response"))), self.response(payload=[]), self.response(payload={"message": "private response"}), self.response(302)):
            with self.subTest(response=response):
                with API(Account("one", cookie("one")), "glados.cloud") as api:
                    with patch.object(api.session, "request", return_value=response):
                        with self.assertRaises(APIError) as raised:
                            api.checkin()
                self.assertNotIn("private response", str(raised.exception))


class OutputTests(CheckinTestCase):
    def test_summary_and_logs_redact_cookie_and_individual_session(self):
        config = self.config(cookies=cookie("private"))
        session_value = "private-session-value"
        message = f"unexpected {config.accounts[0].cookie} {session_value}"
        record = logging.LogRecord("test", logging.INFO, "", 0, "%s", (message,), None)
        logging_config.SecretFilter().filter(record)
        self.assertNotIn(session_value, record.getMessage())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "summary.md"
            os.environ["GITHUB_STEP_SUMMARY"] = str(target)
            checkin.write_summary("测试", [CheckinResult("别名|<script>", "glados.cloud", message=message)])
            summary = target.read_text()
        self.assertNotIn(session_value, summary)
        self.assertNotIn(config.accounts[0].cookie, summary)
        self.assertIn("别名\\|&lt;script&gt;", summary)

    def test_timestamp_is_beijing_time(self):
        formatter = logging_config.BeijingFormatter("%(asctime)s", "%Y-%m-%d %H:%M:%S")
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        record.created = 0
        self.assertEqual(formatter.format(record), "1970-01-01 08:00:00")


if __name__ == "__main__":
    unittest.main()
