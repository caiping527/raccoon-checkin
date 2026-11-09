#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零依赖单元测试：不联网、不读取任何真实令牌。"""

import base64
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

import raccoon_checkin as rc  # noqa: E402
import wecom_notify as wn  # noqa: E402
import summarize_result as sr  # noqa: E402
import update as up  # noqa: E402


def make_jwt(payload):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return "%s.%s.sig" % (b64({"alg": "none"}), b64(payload))


class TestEvaluate(unittest.TestCase):
    TODAY = "2026-11-09"

    def test_grant_today_ok(self):
        grant = {"created_at": "2026-11-09T00:00:01+08:00",
                 "grant_at": "2026-11-09T00:00:01+08:00",
                 "expire_at": "2026-11-16T00:00:00+08:00", "points": 300}
        ok, msg, detail = rc.evaluate(grant, self.TODAY)
        self.assertTrue(ok)
        self.assertIn("已到账", msg)
        self.assertAlmostEqual(detail["valid_hours"], 168, delta=0.1)
        self.assertEqual(detail["expire_at_policy"], "observed_only")

    def test_short_expiry_still_ok(self):
        """用户实测 300 积分不过期：24h 档位不能判失败。"""
        grant = {"created_at": "2026-11-09T00:00:01+08:00",
                 "grant_at": "2026-11-09T00:00:01+08:00",
                 "expire_at": "2026-11-09T23:59:59+08:00", "points": 300}
        ok, msg, detail = rc.evaluate(grant, self.TODAY)
        self.assertTrue(ok)
        self.assertIn("已到账", msg)
        self.assertAlmostEqual(detail["valid_hours"], 24, delta=0.1)

    def test_zero_points_fails(self):
        grant = {"created_at": "2026-11-09T00:00:01+08:00", "points": "0"}
        self.assertFalse(rc.evaluate(grant, self.TODAY)[0])

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

    def test_balance_observations_in_summary(self):
        import io
        from contextlib import redirect_stdout
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_bal.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"status": "ok", "message": "ok",
                       "detail": {"balance": {"daily_points": 300,
                                              "available_points": 9300,
                                              "owner_id": "SHOULD_NOT_LEAK"}}}, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            with mock.patch.object(sys, "argv", ["summarize_result.py", path]):
                sr.main()
        out = buf.getvalue()
        self.assertIn("balance.daily_points", out)
        self.assertIn("9300", out)
        self.assertNotIn("SHOULD_NOT_LEAK", out)
        os.remove(path)


class TestBalance(unittest.TestCase):
    def test_read_balance_ok(self):
        with mock.patch.object(rc, "http_json", return_value=(
                200, {"code": 0, "data": {"available_points": 9300,
                                           "daily_points": 300,
                                           "reward_points": 9000,
                                           "owner_id": 7944725}}, "")):
            bal, err = rc.read_balance("fake")
        self.assertIsNone(err)
        self.assertEqual(bal["daily_points"], 300)
        self.assertNotIn("owner_id", bal)

    def test_read_balance_error(self):
        with mock.patch.object(rc, "http_json", return_value=(500, None, "boom")):
            bal, err = rc.read_balance("fake")
        self.assertIsNone(bal)
        self.assertIn("HTTP 500", err)

    def test_attach_balance_failure_is_nonfatal(self):
        r = {"detail": {}}
        with mock.patch.object(rc, "http_json", return_value=(500, None, "boom")):
            rc.attach_balance(r, "fake")
        self.assertNotIn("balance", r["detail"])
        self.assertIn("balance_error", r["detail"])


class TestOwnership(unittest.TestCase):
    def test_internal_id_matches_phone_label(self):
        t = make_jwt({"sub": "internal-7080", "exp": 1796791687})
        with mock.patch.object(rc, "INTERNAL_ID", "7080"), \
                mock.patch.object(rc, "LABEL", "尾号4060"):
            r = {"detail": {}}
            self.assertTrue(rc.check_ownership(r, t))
        self.assertEqual(r["detail"]["account_tail"], "7080")

    def test_internal_id_mismatch_blocks(self):
        t = make_jwt({"sub": "internal-9999", "exp": 1796791687})
        with mock.patch.object(rc, "INTERNAL_ID", "7080"), \
                mock.patch.object(rc, "LABEL", "尾号4060"):
            r = {"detail": {}}
            self.assertFalse(rc.check_ownership(r, t))
        self.assertIn("归属不符", r["message"])


class TestPhoneLabel(unittest.TestCase):
    def test_masked_phone_tail(self):
        payload = {"code": "OK", "data": {"user": {
            "phone": "134****4060", "name": "RaccoonJoshua"}}}
        with mock.patch.object(up.urllib.request, "urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = \
                json.dumps(payload).encode()
            tail, name, err = up.fetch_phone_tail("fake")
        self.assertEqual((tail, name, err), ("4060", "RaccoonJoshua", None))

    def test_full_phone_tail(self):
        payload = {"data": {"user": {"phone": "13812345678"}}}
        with mock.patch.object(up.urllib.request, "urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = \
                json.dumps(payload).encode()
            self.assertEqual(up.fetch_phone_tail("fake")[0], "5678")

    def test_missing_phone_is_error(self):
        with mock.patch.object(up.urllib.request, "urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = \
                json.dumps({"data": {"user": {}}}).encode()
            tail, _, err = up.fetch_phone_tail("fake")
        self.assertIsNone(tail)
        self.assertIn("未返回手机号", err)

    def test_label_map_and_save_are_idempotent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "账号标签.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# 注释\n7080=4060\n")
            with mock.patch.object(up, "LABEL_FILE", path):
                self.assertEqual(up.load_label_map(), {"7080": "4060"})
                up.save_label("7080", "4060")
                up.save_label("9999", "1234")
                self.assertEqual(up.load_label_map(), {"7080": "4060", "9999": "1234"})
                with open(path, encoding="utf-8") as f:
                    self.assertIn("# 注释", f.read())

    def test_label_is_4_digits_only(self):
        self.assertEqual(up.validate_label("4060"), "4060")
        self.assertIsNone(up.validate_label("abc"))
        self.assertIsNone(up.validate_label("406"))


class TestNotifyNeverLeaksInternalId(unittest.TestCase):
    def test_ownership_failure_message_has_no_internal_tail(self):
        t = make_jwt({"sub": "internal-9999", "exp": 1796791687})
        with mock.patch.object(rc, "INTERNAL_ID", "7080"), \
                mock.patch.object(rc, "LABEL", "手机号尾号4060"):
            r = {"detail": {}}
            self.assertFalse(rc.check_ownership(r, t))
        self.assertNotIn("9999", r["message"])
        self.assertNotIn("7080", r["message"])

    def test_push_text_shows_phone_label(self):
        os.environ.pop("RACCOON_RUN_URL", None)
        text = wn.build_text({"account": "手机号尾号4060", "status": "error",
                              "message": "登录动作触发失败"}, "❌ 签到失败")
        self.assertIn("手机号尾号4060", text)
        self.assertNotIn("7080", text)


class TestPushAppDiagnostics(unittest.TestCase):
    """企微 message/send 返回 invaliduser 时 errcode 仍为 0，日志必须能看出来。"""

    class _FakeTokenResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"errcode":0,"errmsg":"ok","access_token":"FAKE"}'

    def _push(self, reply):
        with mock.patch.object(wn.urllib.request, "urlopen",
                               return_value=self._FakeTokenResponse()), \
                mock.patch.object(wn, "post_json", return_value=reply):
            return wn.push_app("corpid,secret,@all,1000002", "测试")

    def test_success_logs_msgid(self):
        out = self._push({"errcode": 0, "errmsg": "ok", "msgid": "MSG123"})
        self.assertIn("errcode=0", out)
        self.assertIn("msgid=MSG123", out)
        self.assertNotIn("invaliduser", out)

    def test_invaliduser_is_visible(self):
        out = self._push({"errcode": 0, "errmsg": "ok", "invaliduser": "@all"})
        self.assertIn("invaliduser=@all", out)


class TestRevokedTokenMessage(unittest.TestCase):
    """服务端撤销会话时，推送文案必须给出可操作的免登出指引。"""

    def test_401_is_actionable(self):
        captured = {}
        with mock.patch.object(rc, "read_bills",
                               return_value=(None, "HTTP 401 authorization_verify_error")), \
                mock.patch.object(rc, "check_ownership", return_value=True), \
                mock.patch.object(rc, "token_meta", return_value=(20.0, "2026-12-09")), \
                mock.patch.object(rc, "write_result", side_effect=lambda r: captured.update(r)):
            code = rc.diagnose("fake")
        self.assertEqual(code, 1)
        self.assertIn("401", captured["message"])
        self.assertIn("密码登录", captured["message"])
        self.assertTrue(captured["detail"]["revoked"])


class TestIamPasswordLogin(unittest.TestCase):
    """官网同款 AES-128-CFB 加密 + 凭据不外泄（纯离线）。"""

    @classmethod
    def setUpClass(cls):
        import iam_login as il
        cls.il = il

    def test_aes128_fips_vector(self):
        il = self.il
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        pt = bytes.fromhex("00112233445566778899aabbccddeeff")
        rk, nr = il._expand_key(key)
        self.assertEqual(il._encrypt_block(list(pt), rk, nr).hex(),
                         "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_encrypt_credential_shape_and_no_plaintext(self):
        il = self.il
        plain = "13400000000"
        blob = base64.b64decode(il.encrypt_credential(plain))
        self.assertEqual(len(blob), 16 + len(plain.encode()))  # IV + 等长密文
        self.assertNotIn(plain.encode(), blob)

    def test_random_iv(self):
        self.assertNotEqual(self.il.encrypt_credential("abc"),
                            self.il.encrypt_credential("abc"))

    def test_login_request_contains_no_plaintext(self):
        il = self.il
        captured = {}

        class FakeResp:
            status = 200

            def read(self):
                return b'{"access_token":"eyJ.fake.sig"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(req, timeout=None):
            captured["body"] = req.data
            captured["url"] = req.full_url
            return FakeResp()

        with mock.patch.object(il.urllib.request, "build_opener") as bo:
            bo.return_value.open.side_effect = fake_open
            status, data, cookies = il.login_password("13000000000", "PLAINTEXT_SECRET")
        self.assertEqual(status, 200)
        self.assertEqual(data["access_token"], "eyJ.fake.sig")
        self.assertTrue(captured["url"].endswith("/api/iam/v1/auth/login/password/phone"))
        self.assertNotIn(b"13000000000", captured["body"])
        self.assertNotIn(b"PLAINTEXT_SECRET", captured["body"])
        body = json.loads(captured["body"].decode())
        self.assertEqual(sorted(body.keys()), ["nation_code", "password", "phone"])
        self.assertEqual(body["nation_code"], "86")

    def test_cloud_password_mode_overrides_token(self):
        """云端配置密码时，run() 应改用现取的独立令牌。"""
        fake_token = make_jwt({"sub": "user-1234", "exp": 4102444800,
                               "sid": "ses-new-session"})
        captured = {}

        def fake_result():
            return {"status": "pending", "message": "", "detail": {},
                    "account": "手机号尾号4060", "checked_at": "x"}

        with mock.patch.dict(os.environ, {"RACCOON_PHONE": "13400000000",
                                          "RACCOON_PASSWORD": "SECRET",
                                          "RACCOON_ACCESS_TOKEN": "old-token",
                                          "RACCOON_ACCOUNT_ID": "1234"}, clear=False), \
             mock.patch.object(rc, "base_result", side_effect=fake_result), \
             mock.patch.object(sys, "argv", ["raccoon_checkin.py", "--check-only"]), \
             mock.patch("iam_login.login_password",
                        return_value=(200, {"access_token": fake_token}, [])), \
             mock.patch.object(rc, "diagnose", side_effect=lambda t: captured.setdefault("token", t) or 0), \
             mock.patch.object(rc, "write_rotated"):
            rc.run()
        self.assertEqual(captured.get("token"), fake_token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
