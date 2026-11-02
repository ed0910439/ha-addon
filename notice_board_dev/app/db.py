from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .security import hash_password


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> str:
    return os.getenv("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "notice.db"))


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    path = db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _table_sql(c: sqlite3.Connection, name: str) -> str:
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row[0] if row and row[0] else ""


def _columns(c: sqlite3.Connection, name: str) -> set[str]:
    return {r[1] for r in c.execute(f"PRAGMA table_info({name})").fetchall()}


def _migrate_legacy_schema() -> None:
    """Upgrade the first MVP schema in-place without discarding user data."""
    path = db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("PRAGMA journal_mode=WAL")

        user_sql = _table_sql(c, "users")
        if user_sql and ("'VIEWER'" in user_sql or "'EDITOR'" in user_sql or "'PUBLISHER'" in user_sql):
            c.executescript(
                """
                CREATE TABLE users_v2 (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('SYSADMIN','USER','BOT')),
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    totp_secret_enc TEXT,
                    avatar_url TEXT,
                    hass_user_id TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO users_v2 (id,username,display_name,password_hash,role,must_change_password,totp_secret_enc,avatar_url,hass_user_id,is_active,created_at,updated_at)
                SELECT id,username,display_name,password_hash,
                       CASE WHEN role='ADMIN' THEN 'SYSADMIN' ELSE 'USER' END,
                       must_change_password,totp_secret_enc,avatar_url,NULL,is_active,created_at,updated_at
                FROM users;
                DROP TABLE users;
                ALTER TABLE users_v2 RENAME TO users;
                """
            )

        ann_sql = _table_sql(c, "announcements")
        ann_cols = _columns(c, "announcements") if ann_sql else set()
        if ann_sql and ("current_version" not in ann_cols or "'WITHDRAWN'" not in ann_sql):
            c.executescript(
                """
                CREATE TABLE announcements_v2 (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('DRAFT','PUBLISHED','WITHDRAWN','EXPIRED')),
                    current_version INTEGER NOT NULL DEFAULT 0,
                    draft_title TEXT,
                    draft_body TEXT,
                    has_unpublished_changes INTEGER NOT NULL DEFAULT 0,
                    scheduled_publish_at TEXT,
                    expires_at TEXT,
                    created_by TEXT NOT NULL REFERENCES users(id),
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT,
                    withdrawn_at TEXT,
                    expired_at TEXT,
                    deleted_at TEXT
                );
                INSERT INTO announcements_v2 (
                    id,slug,title,body,status,current_version,created_by,updated_by,created_at,updated_at,published_at,deleted_at
                )
                SELECT id,slug,title,body,status,
                       CASE WHEN status='PUBLISHED' THEN 1 ELSE 0 END,
                       created_by,updated_by,created_at,updated_at,published_at,deleted_at
                FROM announcements;
                DROP TABLE announcements;
                ALTER TABLE announcements_v2 RENAME TO announcements;
                """
            )

        if _table_sql(c, "users"):
            user_cols = _columns(c, "users")
            if "hass_user_id" not in user_cols:
                c.execute("ALTER TABLE users ADD COLUMN hass_user_id TEXT")
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_hass_user_id ON users(hass_user_id) WHERE hass_user_id IS NOT NULL"
            )

        if _table_sql(c, "announcements"):
            ann_cols = _columns(c, "announcements")
            if "body_json" not in ann_cols:
                c.execute("ALTER TABLE announcements ADD COLUMN body_json TEXT")
            if "draft_body_json" not in ann_cols:
                c.execute("ALTER TABLE announcements ADD COLUMN draft_body_json TEXT")
            if "importance" not in ann_cols:
                c.execute("ALTER TABLE announcements ADD COLUMN importance TEXT NOT NULL DEFAULT 'NORMAL'")
            if "draft_importance" not in ann_cols:
                c.execute("ALTER TABLE announcements ADD COLUMN draft_importance TEXT")

        if _table_sql(c, "announcement_revisions"):
            rev_cols = _columns(c, "announcement_revisions")
            if "version_no" not in rev_cols:
                c.execute("ALTER TABLE announcement_revisions ADD COLUMN version_no INTEGER")
            if "published_at" not in rev_cols:
                c.execute("ALTER TABLE announcement_revisions ADD COLUMN published_at TEXT")
            if "body_json" not in rev_cols:
                c.execute("ALTER TABLE announcement_revisions ADD COLUMN body_json TEXT")

        if _table_sql(c, "comments"):
            comment_cols = _columns(c, "comments")
            if "version_no" not in comment_cols:
                c.execute("ALTER TABLE comments ADD COLUMN version_no INTEGER")

        c.commit()
    finally:
        c.close()


def init_db() -> None:
    _migrate_legacy_schema()
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('SYSADMIN','USER','BOT')),
                must_change_password INTEGER NOT NULL DEFAULT 1,
                totp_secret_enc TEXT,
                avatar_url TEXT,
                hass_user_id TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_hass_user_id ON users(hass_user_id) WHERE hass_user_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                user_agent TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id, revoked_at);
            CREATE TABLE IF NOT EXISTS auth_challenges (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                stage TEXT NOT NULL,
                next_url TEXT NOT NULL,
                tmp_totp_secret_enc TEXT,
                device_name TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS announcements (
                id TEXT PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                body_json TEXT,
                status TEXT NOT NULL CHECK(status IN ('DRAFT','PUBLISHED','WITHDRAWN','EXPIRED')),
                current_version INTEGER NOT NULL DEFAULT 0,
                importance TEXT NOT NULL DEFAULT 'NORMAL',
                draft_importance TEXT,
                draft_title TEXT,
                draft_body TEXT,
                draft_body_json TEXT,
                has_unpublished_changes INTEGER NOT NULL DEFAULT 0,
                scheduled_publish_at TEXT,
                expires_at TEXT,
                created_by TEXT NOT NULL REFERENCES users(id),
                updated_by TEXT NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at TEXT,
                withdrawn_at TEXT,
                expired_at TEXT,
                deleted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ann_status ON announcements(status, deleted_at, published_at);
            CREATE INDEX IF NOT EXISTS idx_ann_schedule ON announcements(scheduled_publish_at, expires_at, deleted_at);
            CREATE TABLE IF NOT EXISTS announcement_revisions (
                id TEXT PRIMARY KEY,
                announcement_id TEXT NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
                version_no INTEGER,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                body_json TEXT,
                status_snapshot TEXT NOT NULL,
                edited_by TEXT NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                published_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_revisions_ann_version ON announcement_revisions(announcement_id, version_no);
            CREATE TABLE IF NOT EXISTS announcement_reads_v2 (
                announcement_id TEXT NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
                version_no INTEGER NOT NULL,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (announcement_id, version_no, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_reads_version ON announcement_reads_v2(announcement_id, version_no, first_seen_at);
            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                announcement_id TEXT REFERENCES announcements(id) ON DELETE CASCADE,
                upload_session TEXT,
                uploaded_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                original_name TEXT NOT NULL,
                storage_name TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                page_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                attached_at TEXT,
                deleted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_attachments_announcement ON attachments(announcement_id, deleted_at);
            CREATE INDEX IF NOT EXISTS idx_attachments_session ON attachments(upload_session, uploaded_by, deleted_at);
            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                announcement_id TEXT NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                version_no INTEGER,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                actor_user_id TEXT REFERENCES users(id),
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                announcement_id TEXT NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
                version_no INTEGER,
                recipient_username TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','DELIVERED','FAILED')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                last_attempt_at TEXT,
                next_attempt_at TEXT,
                delivered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_retry ON webhook_deliveries(status, next_attempt_at, attempts);
            CREATE TABLE IF NOT EXISTS login_attempts (
                id TEXT PRIMARY KEY,
                attempt_key TEXT NOT NULL,
                failed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_login_attempts_key_time ON login_attempts(attempt_key, failed_at);
            """
        )

        # Backfill Tiptap JSON for v2.1 plaintext content without changing its text projection.
        from .content import dump_doc, legacy_text_doc

        for row in c.execute("SELECT id,body,draft_body,body_json,draft_body_json FROM announcements").fetchall():
            body_json = row["body_json"] or dump_doc(legacy_text_doc(row["body"]))
            draft_json = row["draft_body_json"]
            if row["draft_body"] is not None and not draft_json:
                draft_json = dump_doc(legacy_text_doc(row["draft_body"]))
            c.execute("UPDATE announcements SET body_json=?,draft_body_json=? WHERE id=?", (body_json,draft_json,row["id"]))

        for row in c.execute("SELECT id,body,body_json FROM announcement_revisions").fetchall():
            if not row["body_json"]:
                c.execute("UPDATE announcement_revisions SET body_json=? WHERE id=?", (dump_doc(legacy_text_doc(row["body"])),row["id"]))

        # Migrate first-version read receipts once.
        if _table_sql(c, "announcement_reads"):
            c.execute(
                """
                INSERT OR IGNORE INTO announcement_reads_v2 (announcement_id,version_no,user_id,first_seen_at)
                SELECT r.announcement_id,
                       CASE WHEN a.current_version > 0 THEN a.current_version ELSE 1 END,
                       r.user_id,r.first_seen_at
                FROM announcement_reads r JOIN announcements a ON a.id=r.announcement_id
                WHERE a.current_version > 0
                """
            )

        # Preserve an immutable v1 snapshot for announcements migrated from the MVP.
        migrated = c.execute(
            """
            SELECT a.* FROM announcements a
            WHERE a.current_version=1 AND NOT EXISTS (
                SELECT 1 FROM announcement_revisions r
                WHERE r.announcement_id=a.id AND r.version_no=1
            )
            """
        ).fetchall()
        for a in migrated:
            c.execute(
                """INSERT INTO announcement_revisions
                (id,announcement_id,version_no,title,body,body_json,status_snapshot,edited_by,created_at,published_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), a["id"], 1, a["title"], a["body"], a["body_json"], "PUBLISHED", a["updated_by"], a["published_at"] or a["updated_at"], a["published_at"]),
            )

        count = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if count == 0:
            username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
            password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
            if password:
                t = now_iso()
                c.execute(
                    "INSERT INTO users (id,username,display_name,password_hash,role,must_change_password,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), username, "系統管理員", hash_password(password), "SYSADMIN", 1, t, t),
                )
                print(f"[noticeboard] bootstrap sysadmin created: {username}; password must be changed at first login")
            else:
                print("[noticeboard] no users exist. Set BOOTSTRAP_ADMIN_PASSWORD and restart once to create the first sysadmin.")


def rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def audit(actor_user_id: str | None, action: str, entity_type: str, entity_id: str | None = None, metadata: dict[str, Any] | None = None) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO audit_logs (id,actor_user_id,action,entity_type,entity_id,metadata_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), actor_user_id, action, entity_type, entity_id, json.dumps(metadata or {}, ensure_ascii=False), now_iso()),
        )
