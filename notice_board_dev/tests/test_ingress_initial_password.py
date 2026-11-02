"""DEV regression coverage for Home Assistant SSO first-admin and password setup.

All tests use a temporary SQLite database; HA ingress and the HA directory
are mocked. Never grant authority based solely on client-provided headers.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import conn, init_db, now_iso
from app.security import hash_password, verify_password


class IngressFirstLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="notice-dev-ingress-")
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {
            "DB_PATH": str(Path(self.temp.name) / "notice.db"),
            "BOOTSTRAP_ADMIN_PASSWORD": "",
        })
        env.start()
        self.addCleanup(env.stop)
        init_db()
        self.client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
        self.addCleanup(self.client.close)
        self.headers = {
            "x-ingress-path": "/api/hassio_ingress/dev-test",
            "x-remote-user-id": "hass-test-01",
            "x-remote-user-name": "hassowner",
            "x-remote-user-display-name": "HA Owner",
        }

    def _ingress_get(self, is_admin=True):
        with patch.object(main, "is_ingress_request", return_value=True), patch.object(
            main, "ha_user_is_current_admin", return_value=is_admin
        ):
            return self.client.get("/me/account", headers=self.headers)

    def _ingress_set_password(self, current="", password="NewSecurePassword123!", is_admin=True, csrf=None):
        with patch.object(main, "is_ingress_request", return_value=True), patch.object(
            main, "ha_user_is_current_admin", return_value=is_admin
        ):
            return self.client.post(
                "/me/account/password", headers=self.headers, data={
                    "csrf_token": csrf if csrf is not None else self.client.cookies.get(main.CSRF_COOKIE, ""),
                    "current_password": current,
                    "new_password": password,
                    "confirm": password,
                }
            )

    def _user(self):
        with conn() as c:
            row = c.execute("SELECT * FROM users WHERE hass_user_id=?", ("hass-test-01",)).fetchone()
            return dict(row) if row else None

    def test_first_hass_admin_gets_sysadmin_and_can_set_unknown_initial_password(self):
        page = self._ingress_get(is_admin=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("建立外部登入密碼", page.text)
        self.assertIn("hassowner", page.text)
        self.assertNotIn('name="current_password"', page.text)
        before = self._user()
        self.assertEqual(before["role"], "SYSADMIN")
        self.assertEqual(before["must_change_password"], 1)
        r = self._ingress_set_password()
        self.assertEqual(r.status_code, 200)
        self.assertIn("已設定外部登入密碼", r.text)
        after = self._user()
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["role"], "SYSADMIN")
        self.assertEqual(after["must_change_password"], 0)
        self.assertTrue(verify_password("NewSecurePassword123!", after["password_hash"]))
        self.assertIn('name="current_password"', r.text)
        # Direct login still requires 2FA enrollment, not an unverified session.
        r = self.client.post("/auth/login", data={
            "csrf_token": self.client.cookies.get(main.CSRF_COOKIE, ""),
            "username": "hassowner",
            "password": "NewSecurePassword123!",
            "next": "/",
        })
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/setup/2fa")

    def test_existing_ingress_user_is_recovered_without_db_reset(self):
        first = self._ingress_get(is_admin=False)
        self.assertEqual(first.status_code, 200)
        user = self._user()
        self.assertEqual(user["role"], "USER")
        self.assertEqual(user["must_change_password"], 1)
        second = self._ingress_get(is_admin=True)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self._user()["id"], user["id"])
        self.assertEqual(self._user()["role"], "SYSADMIN")
        self.assertEqual(self._ingress_set_password().status_code, 200)

    def test_non_admin_does_not_gain_admin_privileges(self):
        self._ingress_get(is_admin=False)
        self.assertEqual(self._user()["role"], "USER")
        self.assertEqual(self._ingress_set_password(is_admin=False).status_code, 200)
        self.assertEqual(self._user()["role"], "USER")

    def test_bootstrap_does_not_override_existing_sysadmin(self):
        with conn() as c:
            c.execute(
                "INSERT INTO users (id,username,display_name,password_hash,role,must_change_password,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                ("existing", "admin", "Existing", hash_password("AnExistingPassword123!"), "SYSADMIN", 0, now_iso(), now_iso()),
            )
        self._ingress_get(is_admin=True)
        self.assertEqual(self._user()["role"], "USER")

    def test_untrusted_ingress_headers_do_not_create_user(self):
        with patch.object(main, "trusted_ingress_proxy_addresses", return_value=set()):
            resp = self.client.get("/me/account", headers=self.headers)
        self.assertEqual(resp.status_code, 303)
        self.assertIsNone(self._user())

    def test_unknown_old_password_cannot_be_bypassed_over_direct_session(self):
        self._ingress_get(is_admin=True)
        with conn() as c:
            u = self._user()
            n = now_iso()
            expiry = datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat()
            from app.security import token_hash
            c.execute(
                "INSERT INTO devices (id,user_id,name,token_hash,first_seen_at,last_seen_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                ("dev-test-device", u["id"], "test", token_hash("dev-device-cookie"), n, n, expiry),
            )
        self.client.cookies.set(main.DEVICE_COOKIE, "dev-device-cookie")
        response = self.client.post("/me/account/password", data={
            "csrf_token": self.client.cookies.get(main.CSRF_COOKIE, ""),
            "new_password": "ChangedPassword123!",
            "confirm": "ChangedPassword123!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("目前密碼不正確", response.text)
        self.assertEqual(self._user()["must_change_password"], 1)

    def test_initialized_password_requires_old_password_even_via_ingress(self):
        self._ingress_get()
        self._ingress_set_password()
        denied = self._ingress_set_password(current="")
        self.assertEqual(denied.status_code, 200)
        self.assertIn("目前密碼不正確", denied.text)
        ok = self._ingress_set_password(current="NewSecurePassword123!", password="AnotherSecurePassword456!")
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(verify_password("AnotherSecurePassword456!", self._user()["password_hash"]))

    def test_invalid_csrf_cannot_set_password(self):
        self._ingress_get()
        old = self._user()["password_hash"]
        r = self._ingress_set_password(csrf="invalid")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self._user()["password_hash"], old)


if __name__ == "__main__":
    unittest.main()
