"""2.6.1 regression coverage. Run with python -m unittest discover -s tests -v."""
from __future__ import annotations
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main
from app.db import conn, init_db
from app.security import hash_password, token_hash


class Notice261Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="notice-261-")
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            "DB_PATH": str(Path(self.tmp.name) / "notice.db"),
            "SUPERVISOR_TOKEN": "test-supervisor-token",
        })
        env.start()
        self.addCleanup(env.stop)
        init_db()
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with conn() as c:
            c.execute("INSERT INTO users (id,username,display_name,password_hash,role,must_change_password,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      ("u1", "alice", "Alice", hash_password("TestPassword12345"), "SYSADMIN", 0, now, now))
            c.execute("INSERT INTO devices (id,user_id,name,token_hash,first_seen_at,last_seen_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                      ("d1", "u1", "test-device", token_hash("alice-cookie"), now, now, expires))
            c.execute("INSERT INTO announcements (id,slug,title,body,status,current_version,created_by,updated_by,created_at,updated_at,published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      ("a1", "release-261", "公告標題", "公告完整內文", "PUBLISHED", 1, "u1", "u1", now, now, now))
        self.client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
        self.addCleanup(self.client.close)
        self.client.cookies.set(main.DEVICE_COOKIE, "alice-cookie")
        self.client.get("/")
        self.csrf = self.client.cookies.get(main.CSRF_COOKIE)

    def test_reading_home_or_detail_alone_does_not_mark_read(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn('data-notice-accordion', home.text)
        self.assertIn('data-notice-content', home.text)
        self.assertIn('公告標題', home.text)
        self.assertIn('importance-normal', home.text)
        detail = self.client.get("/a/release-261")
        self.assertEqual(detail.status_code, 200)
        with conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM announcement_reads_v2").fetchone()[0], 0)

    def test_receipt_is_csrf_protected_and_version_locked(self):
        path = "/api/announcements/a1/read"
        self.assertEqual(self.client.post(path, data={"version": 1, "csrf_token": "bad"}).status_code, 403)
        self.assertEqual(self.client.post(path, data={"version": 2, "csrf_token": self.csrf}).status_code, 409)
        first = self.client.post(path, data={"version": 1, "csrf_token": self.csrf})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["read_count"], 1)
        again = self.client.post(path, data={"version": 1, "csrf_token": self.csrf})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["read_count"], 1)

    def test_importance_preserves_existing_announcements(self):
        with conn() as c:
            a = c.execute("SELECT importance,draft_importance FROM announcements WHERE id='a1'").fetchone()
            self.assertEqual(a["importance"], "NORMAL")
            self.assertIsNone(a["draft_importance"])
        form = self.client.get("/manage/announcements/a1/edit")
        self.assertIn('name="importance"', form.text)

    def test_hass_admin_picker_requires_server_verified_admin(self):
        ha_users = [{"id": "ha-admin-1", "name": "HA Owner", "username": "owner", "is_owner": True}]
        with patch.object(main, "list_ha_administrators", return_value=ha_users):
            page = self.client.get("/manage/hass-admin")
            self.assertEqual(page.status_code, 200)
            self.assertIn("HA Owner", page.text)
            rejected = self.client.post("/manage/hass-admin/bind", data={
                "hass_user_id": "user-injected-id", "csrf_token": self.csrf,
            })
            self.assertEqual(rejected.status_code, 400)
            ok = self.client.post("/manage/hass-admin/bind", data={
                "hass_user_id": "ha-admin-1", "csrf_token": self.csrf,
            })
            self.assertEqual(ok.status_code, 303)
            with conn() as c:
                self.assertEqual(c.execute("SELECT hass_user_id FROM users WHERE id='u1'").fetchone()[0], "ha-admin-1")


if __name__ == "__main__":
    unittest.main()
