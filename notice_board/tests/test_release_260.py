"""Release regressions. Run: python -m unittest discover -s tests -v.

Requires httpx (FastAPI TestClient). Every test uses a temporary database;
no preview or deployed database is read or changed.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# Avoid creating uploads or database files in the source tree during import.
_IMPORT_ROOT = tempfile.TemporaryDirectory(prefix="notice-import-")
os.environ.setdefault("UPLOAD_DIR", str(Path(_IMPORT_ROOT.name) / "uploads"))
from fastapi.testclient import TestClient
from app import main
from app.db import conn, init_db
from app.security import hash_password, token_hash


class Release260Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "ReleaseTestPassword123!"
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="notice-test-")
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"DB_PATH": str(Path(self.tmp.name) / "test.db")})
        env.start()
        self.addCleanup(env.stop)
        init_db()
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with conn() as c:
            for uid in ("owner", "other"):
                c.execute("INSERT INTO users (id,username,display_name,password_hash,role,must_change_password,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (uid, uid, uid, self.password_hash, "USER", 0, now, now))
            for did, uid, raw in (("current", "owner", "test-cookie"), ("target", "owner", "target-cookie"), ("foreign", "other", "foreign-cookie")):
                c.execute("INSERT INTO devices (id,user_id,name,token_hash,first_seen_at,last_seen_at,expires_at) VALUES (?,?,?,?,?,?,?)", (did, uid, did, token_hash(raw), now, now, expires))
            for aid, status, pending in (("published", "PUBLISHED", 0), ("pending", "PUBLISHED", 1), ("draft", "DRAFT", 1), ("withdrawn", "WITHDRAWN", 0)):
                c.execute("INSERT INTO announcements (id,slug,title,body,status,current_version,draft_title,draft_body,has_unpublished_changes,created_by,updated_by,created_at,updated_at,published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (aid, aid, "LIVE_" + aid, "LIVE_BODY_" + aid, status, 1 if status == "PUBLISHED" else 0, "DRAFT_TITLE_" + aid, "DRAFT_BODY_" + aid, pending, "owner", "owner", now, now, now if status == "PUBLISHED" else None))
        self.client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
        self.addCleanup(self.client.close)
        self.client.cookies.set(main.DEVICE_COOKIE, "test-cookie")
        self.client.get("/")
        self.csrf = self.client.cookies.get(main.CSRF_COOKIE)

    def revoked(self, did):
        with conn() as c:
            return c.execute("SELECT revoked_at FROM devices WHERE id=?", (did,)).fetchone()["revoked_at"]

    def revoke(self, password, did="target"):
        return self.client.post(f"/me/devices/{did}/revoke", data={"csrf_token": self.csrf, "current_password": password})

    def test_dashboard_only_contains_published_content(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("LIVE_published", response.text)
        self.assertIn("LIVE_pending", response.text)
        self.assertNotIn("LIVE_draft", response.text)
        self.assertNotIn("LIVE_withdrawn", response.text)
        self.assertNotIn("DRAFT_TITLE_", response.text)
        self.assertNotIn("DRAFT_BODY_", response.text)

    def test_home_navigation_and_brand_indicator(self):
        html = self.client.get("/").text
        brand = html.split('class="brand"', 1)[1].split("</a>", 1)[0]
        self.assertIn("data-unread-badge", brand)
        nav = html.split('class="nav pro-layout-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("nav-current", nav)
        self.assertNotIn("回首頁", nav)
        self.assertNotIn("解除此裝置", nav)
        self.assertIn("設定", nav)
        self.assertIn("回首頁", self.client.get("/me/account").text)

    def test_footer_links_are_real_pages(self):
        for path in ("/terms", "/user-terms"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(str(datetime.now(main.DISPLAY_TZ).year), response.text)

    def test_wrong_or_missing_password_cannot_revoke(self):
        for password in ("", "wrong"):
            response = self.revoke(password)
            self.assertEqual(response.status_code, 200)
            self.assertIn("未解除裝置", response.text)
            self.assertIsNone(self.revoked("target"))

    def test_correct_password_revokes_owned_device(self):
        response = self.revoke(self.password)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/me/devices")
        self.assertIsNotNone(self.revoked("target"))
        self.assertIsNone(self.revoked("current"))

    def test_current_device_revoke_clears_cookie(self):
        response = self.revoke(self.password, "current")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertIsNotNone(self.revoked("current"))

    def test_foreign_device_cannot_be_revoked(self):
        response = self.revoke(self.password, "foreign")
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.revoked("foreign"))

    def test_legacy_logout_cannot_bypass_password(self):
        response = self.client.post("/logout", data={"csrf_token": self.csrf})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/me/devices")
        self.assertIsNone(self.revoked("current"))

    def test_csrf_required_for_device_revoke(self):
        response = self.client.post("/me/devices/target/revoke", data={"current_password": self.password, "csrf_token": "invalid"})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.revoked("target"))

    def test_password_reverification_is_rate_limited(self):
        for _ in range(5):
            self.revoke("wrong")
        response = self.revoke(self.password)
        self.assertIn("驗證嘗試過多", response.text)
        self.assertIsNone(self.revoked("target"))

    def test_ingress_brand_and_device_session_presentation(self):
        with conn() as c:
            user = dict(c.execute("SELECT * FROM users WHERE id='owner'").fetchone())
        user.update(device_id=None, auth_source="INGRESS")
        with patch.object(main, "current_user", return_value=user), patch.object(main, "is_ingress_request", return_value=True):
            response = self.client.get("/me/devices")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Home Assistant", response.text)
        self.assertNotIn("目前裝置</span>", response.text)
        self.assertNotIn("解除此裝置", response.text)

    def test_hidden_state_and_confirmation_are_present(self):
        css = (main.BASE_DIR / "static/style.css").read_text()
        self.assertIn("[hidden] { display: none !important; }", css)
        html = self.client.get("/me/devices").text
        self.assertIn('data-confirm="確定解除這台裝置', html)
        self.assertIn('name="current_password"', html)


if __name__ == "__main__":
    unittest.main()
