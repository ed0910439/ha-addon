"""Notice Board 2.6.2 visual editor & Content Security Policy tests.

These tests are deliberately offline: no CDN requests or production database access.
"""
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


class Editor262Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="notice-262-")
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"DB_PATH": str(Path(self.tmp.name) / "notice.db")})
        env.start()
        self.addCleanup(env.stop)
        init_db()
        now = datetime.now(timezone.utc).isoformat()
        expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with conn() as c:
            c.execute(
                "INSERT INTO users (id,username,display_name,password_hash,role,must_change_password,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                ("editor-262", "editor262", "Editor", hash_password("ExampleTestPassword123!"), "SYSADMIN", 0, now, now),
            )
            c.execute(
                "INSERT INTO devices (id,user_id,name,token_hash,first_seen_at,last_seen_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                ("device-262", "editor-262", "test", token_hash("trusted-262"), now, now, expiry),
            )
        self.client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
        self.addCleanup(self.client.close)
        self.client.cookies.set(main.DEVICE_COOKIE, "trusted-262")
        self.client.get("/")

    def test_icon_toolbar_and_md_switch_are_accessible(self):
        r = self.client.get("/manage/announcements/new")
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-markdown-toggle', r.text)
        self.assertIn('role="switch"', r.text)
        self.assertIn('data-markdown-source', r.text)
        self.assertIn('data-markdown-panel hidden', r.text)
        for command in ("bold", "italic", "strike", "heading2", "heading3", "bulletList",
                        "orderedList", "blockquote", "horizontalRule", "undo", "redo"):
            self.assertIn(f'data-command="{command}"', r.text)
        self.assertGreaterEqual(r.text.count('class="editor-icon"'), 12)
        self.assertIn('data-upload-pdf', r.text)
        self.assertIn('name="body_json"', r.text)

    def test_csp_allows_specific_sourcemap_origin_not_unsafe_inline(self):
        r = self.client.get("/")
        csp = r.headers.get("content-security-policy", "")
        self.assertIn("connect-src 'self' https://cdn.jsdelivr.net", csp)
        self.assertIn("script-src 'self' https://cdn.jsdelivr.net", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)

    def test_existing_json_storage_and_no_new_md_database_columns(self):
        with conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(announcements)")}
            self.assertIn("body_json", cols)
            self.assertIn("draft_body_json", cols)
            self.assertNotIn("raw_markdown", cols)

if __name__ == "__main__":
    unittest.main()
