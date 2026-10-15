import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from fastapi import FastAPI, Header, HTTPException, Request


ALLOWED_EVENTS = {
    "announcement.published",
    "announcement.updated",
    "announcement.withdrawn",
    "announcement.expired",
}

REQUIRED_FIELDS = {
    "event",
    "event_id",
    "announcement_id",
    "slug",
    "version",
    "title",
    "url",
    "source",
    "recipient",
}


@dataclass(frozen=True)
class GatewaySettings:
    shared_secret: str
    hass_webhook_url: str
    hass_timeout_seconds: float
    ntfy_base_url: str
    ntfy_default_topic: str
    ntfy_topic_map: dict[str, str]
    ntfy_access_token: str
    ntfy_username: str
    ntfy_password: str
    ntfy_timeout_seconds: float
    dedupe_ttl_seconds: int
    hass_bearer_token: str = ""

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        topic_map_raw = os.getenv("GATEWAY_NTFY_TOPIC_MAP", "{}")
        try:
            topic_map = json.loads(topic_map_raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GATEWAY_NTFY_TOPIC_MAP must be valid JSON") from exc
        if not isinstance(topic_map, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in topic_map.items()):
            raise RuntimeError("GATEWAY_NTFY_TOPIC_MAP must be a JSON object of string keys and values")

        return cls(
            shared_secret=os.getenv("GATEWAY_SHARED_SECRET", ""),
            hass_webhook_url=os.getenv("GATEWAY_HASS_WEBHOOK_URL", ""),
            hass_timeout_seconds=float(os.getenv("GATEWAY_HASS_TIMEOUT_SECONDS", "5")),
            ntfy_base_url=os.getenv("GATEWAY_NTFY_BASE_URL", "https://ntfy.sh").rstrip("/"),
            ntfy_default_topic=os.getenv("GATEWAY_NTFY_DEFAULT_TOPIC", ""),
            ntfy_topic_map=topic_map,
            ntfy_access_token=os.getenv("GATEWAY_NTFY_ACCESS_TOKEN", ""),
            ntfy_username=os.getenv("GATEWAY_NTFY_USERNAME", ""),
            ntfy_password=os.getenv("GATEWAY_NTFY_PASSWORD", ""),
            ntfy_timeout_seconds=float(os.getenv("GATEWAY_NTFY_TIMEOUT_SECONDS", "5")),
            dedupe_ttl_seconds=int(os.getenv("GATEWAY_DEDUPE_TTL_SECONDS", "86400")),
            hass_bearer_token=os.getenv("GATEWAY_HASS_BEARER_TOKEN", ""),
        )


class HttpPoster:
    def post_json(self, url: str, payload: dict[str, Any], timeout_seconds: float, headers: dict[str, str] | None = None) -> int:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return int(response.status)
        except error.HTTPError as exc:
            return int(exc.code)
        except (TimeoutError, OSError) as exc:
            raise RuntimeError(str(exc)) from exc


class NotificationGateway:
    def __init__(self, settings: GatewaySettings, poster: HttpPoster | None = None):
        self.settings = settings
        self.poster = poster or HttpPoster()
        self._seen_event_ids: dict[str, float] = {}

    def handle_event(self, event: dict[str, Any]) -> dict[str, Any]:
        self._validate_event(event)
        self._prune_seen()

        event_id = str(event["event_id"])
        if event_id in self._seen_event_ids:
            return {"status": "duplicate", "event_id": event_id}

        hass_result = self._send_hass(event)
        if hass_result["ok"]:
            self._mark_seen(event_id)
            return {"status": "delivered", "primary": "hass", "event_id": event_id, "hass": hass_result}

        ntfy_result = self._send_ntfy(event)
        if ntfy_result["ok"]:
            self._mark_seen(event_id)
            return {
                "status": "delivered",
                "primary": "ntfy",
                "event_id": event_id,
                "hass": hass_result,
                "ntfy": ntfy_result,
            }

        return {"status": "failed", "event_id": event_id, "hass": hass_result, "ntfy": ntfy_result}

    def _validate_event(self, event: dict[str, Any]) -> None:
        missing = sorted(field for field in REQUIRED_FIELDS if field not in event or event[field] in (None, ""))
        if missing:
            raise HTTPException(status_code=422, detail={"error": "missing required fields", "fields": missing})
        if event["event"] not in ALLOWED_EVENTS:
            raise HTTPException(status_code=422, detail={"error": "unsupported event", "event": event["event"]})

    def _send_hass(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.hass_webhook_url:
            return {"ok": False, "reason": "hass_webhook_url_not_configured"}
        headers = {}
        if self.settings.hass_bearer_token:
            headers["Authorization"] = f"Bearer {self.settings.hass_bearer_token}"
        try:
            status = self.poster.post_json(
                self.settings.hass_webhook_url,
                event,
                self.settings.hass_timeout_seconds,
                headers,
            )
        except RuntimeError as exc:
            return {"ok": False, "reason": "hass_request_failed", "error": str(exc)}
        return {"ok": 200 <= status < 300, "status_code": status}

    def _send_ntfy(self, event: dict[str, Any]) -> dict[str, Any]:
        topic = self.settings.ntfy_topic_map.get(str(event["recipient"]), self.settings.ntfy_default_topic)
        if not topic:
            return {"ok": False, "reason": "ntfy_topic_not_configured"}

        payload = {
            "topic": topic,
            "title": self._ntfy_title(event),
            "message": self._ntfy_message(event),
            "click": event["url"],
            "tags": ["loudspeaker"],
            "priority": 3,
        }
        headers = self._ntfy_auth_headers()
        try:
            status = self.poster.post_json(self.settings.ntfy_base_url, payload, self.settings.ntfy_timeout_seconds, headers)
        except RuntimeError as exc:
            return {"ok": False, "reason": "ntfy_request_failed", "error": str(exc)}
        return {"ok": 200 <= status < 300, "status_code": status, "topic": topic}

    def _ntfy_auth_headers(self) -> dict[str, str]:
        if self.settings.ntfy_access_token:
            return {"Authorization": f"Bearer {self.settings.ntfy_access_token}"}
        if self.settings.ntfy_username and self.settings.ntfy_password:
            raw = f"{self.settings.ntfy_username}:{self.settings.ntfy_password}".encode("utf-8")
            return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}
        return {}

    def _prune_seen(self) -> None:
        now = time.monotonic()
        expired = [event_id for event_id, expires_at in self._seen_event_ids.items() if expires_at <= now]
        for event_id in expired:
            self._seen_event_ids.pop(event_id, None)

    def _mark_seen(self, event_id: str) -> None:
        self._seen_event_ids[event_id] = time.monotonic() + self.settings.dedupe_ttl_seconds

    @staticmethod
    def _ntfy_title(event: dict[str, Any]) -> str:
        labels = {
            "announcement.published": "公告發布",
            "announcement.updated": "公告更新",
            "announcement.withdrawn": "公告撤回",
            "announcement.expired": "公告到期",
        }
        return f"{labels[event['event']]}：{event['title']}"

    @staticmethod
    def _ntfy_message(event: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"event={event['event']}",
                f"announcement_id={event['announcement_id']}",
                f"slug={event['slug']}",
                f"version={event['version']}",
                f"source={event['source']}",
                f"url={event['url']}",
            ]
        )


def create_app(settings: GatewaySettings | None = None, poster: HttpPoster | None = None) -> FastAPI:
    gateway = NotificationGateway(settings or GatewaySettings.from_env(), poster)
    app = FastAPI(title="Notice Board Notification Gateway")
    app.state.gateway = gateway

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/webhooks/noticeboard")
    async def receive_noticeboard_event(request: Request, x_notification_gateway_token: str | None = Header(default=None)):
        if gateway.settings.shared_secret and x_notification_gateway_token != gateway.settings.shared_secret:
            raise HTTPException(status_code=401, detail="invalid gateway token")

        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON payload") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be a JSON object")

        result = gateway.handle_event(payload)
        status_code = 200 if result["status"] in {"delivered", "duplicate"} else 502
        if status_code != 200:
            raise HTTPException(status_code=status_code, detail=result)
        return result

    return app


app = create_app()

