"""Read eligible Home Assistant administrators through the Supervisor's privileged WebSocket proxy.

Never accept usernames or browser-provided role flags as proof of HA administration.
This module is only available when the add-on has homeassistant_api: true.
"""
from __future__ import annotations

import json
import os
from time import monotonic


class HAAdminDirectoryUnavailable(RuntimeError):
    pass


def list_ha_administrators() -> list[dict[str, str | bool]]:
    supervisor_token = os.getenv("SUPERVISOR_TOKEN", "").strip()
    if not supervisor_token:
        raise HAAdminDirectoryUnavailable("無法使用 Supervisor Token。此功能僅支援 HA 外掛環境。")

    try:
        from websockets.sync.client import connect

        with connect("ws://supervisor/core/websocket", open_timeout=5, close_timeout=2, max_size=1_000_000) as ws:
            hello = json.loads(ws.recv(timeout=5))
            if hello.get("type") != "auth_required":
                raise HAAdminDirectoryUnavailable("Home Assistant WebSocket 未提供預期的驗證流程。")
            ws.send(json.dumps({"type": "auth", "access_token": supervisor_token}))
            auth_reply = json.loads(ws.recv(timeout=5))
            if auth_reply.get("type") != "auth_ok":
                raise HAAdminDirectoryUnavailable("Home Assistant 拒絕 Supervisor 驗證。")
            ws.send(json.dumps({"id": 1, "type": "config/auth/list"}))
            response = json.loads(ws.recv(timeout=5))
            if response.get("type") != "result" or response.get("id") != 1 or response.get("success") is not True:
                raise HAAdminDirectoryUnavailable("HA 未授權讀取管理員名單，請確認外掛的 homeassistant_api 權限。")
    except HAAdminDirectoryUnavailable:
        raise
    except Exception as exc:
        raise HAAdminDirectoryUnavailable("無法連接 Home Assistant 管理員清單；未修改任何帳號。") from exc

    users = response.get("result")
    if not isinstance(users, list):
        raise HAAdminDirectoryUnavailable("Home Assistant 回傳的帳號資料格式不正確。")

    admins = []
    for user in users:
        if not isinstance(user, dict):
            continue
        uid = user.get("id")
        if not isinstance(uid, str) or not uid or not user.get("is_active", True) or user.get("system_generated", False):
            continue
        if not (user.get("is_owner") or "system-admin" in (user.get("group_ids") or [])):
            continue
        admins.append({
            "id": uid,
            "username": str(user.get("username") or ""),
            "name": str(user.get("name") or user.get("username") or "未命名管理員"),
            "is_owner": bool(user.get("is_owner")),
        })
    return sorted(admins, key=lambda user: (user["name"].casefold(), user["id"]))


# A short cache avoids creating a WebSocket on every Ingress page request for a linked sysadmin.
_admin_cache: dict[str, object] = {"expires": 0.0, "ids": set()}


def ha_user_is_current_admin(hass_user_id: str) -> bool:
    if not hass_user_id:
        return False
    now = monotonic()
    if now >= float(_admin_cache["expires"]):
        try:
            ids = {a["id"] for a in list_ha_administrators()}
        except HAAdminDirectoryUnavailable:
            _admin_cache.update(expires=0.0, ids=set())
            return False  # Fail closed; direct Notice Board admin login remains available.
        _admin_cache.update(expires=now + 30.0, ids=ids)
    return hass_user_id in _admin_cache["ids"]


def reset_admin_cache() -> None:
    _admin_cache.update(expires=0.0, ids=set())
