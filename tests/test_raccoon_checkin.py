#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零依赖单元测试：不联网、不读取任何真实令牌。"""

import base64
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import raccoon_checkin as rc  # noqa: E402
import wecom_notify as wn  # noqa: E402
import summarize_result as sr  # noqa: E402


def make_jwt(payload):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return "%s.%s.sig" % (b64({"alg": "none"}), b64(payload))


class TestEvaluate(unittest.TestCase):
    TODAY = "2026-11-09"

    def test_extended(self):
        grant = {"created_at": "2026-11-09T00:00:01+08:00",
                 "grant_at": "2026-11-09T00:00:01+08:00",
                 "expire_at": "2026-11-16T00:00:00+08:00", "points": 300}
        ok, msg, detail = rc.evaluate(grant, self.TODAY)
        self.assertTrue(ok)
        self.assertIn("已延长", msg)
        self.assertAlmostEqual(detail["valid_hours"], 168, delta=0.1)

    def test_not_extended(self):
        grant = {"created_at": "2026-11-09T00:00:01+08:00",
                 "grant_at": "2026-11-09T00:00:01+08:00",
                 "expire_at": "2026-11-09T23:59:59+08:00", "points": 300}
        ok, msg, _ = rc.evaluate(grant, self.TODAY)
        self.assertFalse(ok)
        self.assertIn("未延长", msg)

    def test_yesterday_record_is_not_today(self):
        grant = {"created_at": "2026-11-08T00:00:01+08:00",
                 "grant_at": "2026-11-08T00:00:01+08:00",
                 "expire_at": "2026-11-15T00:00:00+08:00"}
        ok, msg, _ = rc.evaluate(grant, self.TODAY)
        self.assertFalse(ok)
        self.assertIn("今日未发放", msg)

    def test_missing_record(self):
        self.assertFalse(rc.evaluate(None, self.TODAY)[0])
        self.assertFalse(rc.evaluate({}, self.TODAY)[0])

    def test_bad_timestamps(self):
        grant = {"created_at": "2026-11-09", "grant_at": "x", "expire_at": "y"}
        self.assertFalse(rc.evaluate(grant, self.TODAY)[0])


class TestTokenHelpers(unittest.TestCase):
    def test_jwt_and_tail(self):
        t = make_jwt({"sub": "13800007080", "exp": 1796791687})
        self.assertEqual(rc.account_tail(t), "7080")
        self.assertIn("exp", rc.jwt_payload(t))

    def test_invalid_token_is_safe(self):
        self.assertEqual(rc.jwt_payload("not-a-jwt"), {})
        self.assertEqual(rc.account_tail(""), "")

    def test_parse_dt_timezone(self):
        self.assertIsNotNone(rc.parse_dt("2026-11-09T00:00:01+08:00"))
        self.assertIsNotNone(rc.parse_dt("2026-11-09T00:00:01Z"))
        self.assertIsNone(rc.parse_dt(None))


class TestNotifyPolicy(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SUCCESS_NOTIFY", None)

    def test_success_silent(self):
        self.assertIsNone(wn.decide({"status": "ok", "token_days_left": 30}))

    def test_expiring_warns(self):
        self.assertEqual(wn.decide({"status": "ok", "token_days_left": 5}), "⚠️ 令牌临期")

    def test_failure_notifies(self):
        self.assertEqual(wn.decide({"status": "error"}), "❌ 签到失败")

    def test_force_success(self):
        os.environ["SUCCESS_NOTIFY"] = "1"
        self.assertEqual(wn.decide({"status": "ok", "token_days_left": 30}), "✅ 签到成功")

    def test_missing_result_is_failure(self):
        r = wn.load_result("/definitely/not/exists.json")
        self.assertEqual(r["status"], "error")
        self.assertEqual(wn.decide(r), "❌ 签到失败")


class TestTrigger(unittest.TestCase):
    def test_setting_info_ok(self):
        with mock.patch.object(rc, "http_json", return_value=(200, {"code": 0}, "")):
            ok, detail = rc.trigger_login("fake")
        self.assertTrue(ok)
        self.assertEqual(detail["code"], 0)

    def test_setting_info_business_error(self):
        with mock.patch.object(rc, "http_json", return_value=(200, {"code": 401, "message": "expired"}, "")):
            ok, detail = rc.trigger_login("fake")
        self.assertFalse(ok)
        self.assertEqual(detail["code"], 401)

    def test_grant_fallback_shape(self):
        with mock.patch.object(rc, "http_json", side_effect=[
            (200, {"code": 0}, ""),
            (200, {"code": 0, "data": {"granted": True}}, ""),
        ]):
            ok, detail = rc.trigger_login("fake", use_grant=True)
        self.assertTrue(ok)
        self.assertTrue(detail["grant"]["granted"])


class TestSummary(unittest.TestCase):
    def test_whitelist_output(self):
        import io
        from contextlib import redirect_stdout
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_result.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"status": "ok", "message": "ok", "detail": {},
                       "secret": "SHOULD_NOT_LEAK", "token_days_left": 30}, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            with mock.patch.object(sys, "argv", ["summarize_result.py", path]):
                sr.main()
        out = buf.getvalue()
        self.assertIn("status", out)
        self.assertNotIn("SHOULD_NOT_LEAK", out)
        os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
