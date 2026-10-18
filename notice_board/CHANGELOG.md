# Changelog

## 2.5.4

- Add an atomic "save and publish" path so the exact editor contents being viewed are written before a new announcement version is created.
- Keep the legacy publish endpoint for republishing an unchanged withdrawn/expired announcement, while directing normal edits through the safer combined action.
- Add a Home Assistant in-app path (`/app/notice_board/a/<slug>`) to notification payloads while retaining the public URL for external links and ntfy.
- Open Home Assistant persistent notifications through the in-app route instead of the public browser URL.
- Add Home Assistant panel route synchronization so deep links can open a specific Notice Board announcement inside the Companion App without putting authentication tokens in URLs.
- Document `url` + Android `clickAction` usage with the relative `hass_path` for Companion App notifications.


## 2.5.3

- Fix Home Assistant Ingress SSO trust by preserving the real socket peer seen by the Notice Board process.
- Stop Uvicorn from trusting arbitrary `X-Forwarded-For` values with `--forwarded-allow-ips "*"`; this previously replaced the Supervisor peer address with the browser/client IPv6 address.
- Keep SSO gated by the trusted Supervisor peer plus `X-Ingress-Path` and `X-Remote-User-Id`.
- Public/direct Notice Board access continues to use the configured public URL, password/TOTP, and Cloudflare-specific client IP handling where enabled.


## 2.5.2

- Preserve Home Assistant Ingress path routing even when SSO identity validation fails, preventing redirects to Home Assistant's root `/login` and the resulting 404.
- Keep Ingress presentation detection separate from trusted SSO identity validation; untrusted headers still cannot grant a Notice Board login.
- Add a minimal Ingress diagnostic warning that records only peer/trust/header-presence flags, without logging the Home Assistant user ID or Ingress token.


## 2.5.1

- Fix Home Assistant Ingress opening to a 404 when the Supervisor proxy address is not the previously assumed fixed IP.
- Resolve the trusted Supervisor address dynamically through the internal `supervisor` hostname, while still requiring Home Assistant Ingress identity headers.
- Keep direct/public Notice Board authentication unchanged.


## 2.5.0

- Add Home Assistant Ingress panel support with sidebar title and bullhorn icon.
- Add Ingress SSO using Supervisor-authenticated Home Assistant user headers.
- Automatically link an unbound same-name Notice Board account, or provision a normal user on first Ingress access.
- Keep public/direct Notice Board login, password, TOTP and trusted-device authentication unchanged.
- Add per-user unread state and a live unread dot in the Notice Board navigation.
- Add Home Assistant persistent notifications for published/updated announcements and dismiss them on withdrawal/expiry.
- Keep recipient-specific Home Assistant webhook delivery for mobile push and custom automations.
- Add account-management controls for Home Assistant identity links.
- Make templates, redirects, PDF access and editor requests work under the dynamic Ingress path.


## 2.4.1

- Allow system administrators to create, edit, publish, withdraw, and delete their own announcements.
- Keep administrators from editing announcements created by other users.
- Show administrator-owned drafts and pending updates on the dashboard.
- Allow administrator-owned unpublished PDF attachments to remain accessible to their owner.


## 2.4.0

- Initial private Home Assistant Local App package.
- Runs Notice Board and Notification Gateway as separate supervised processes in one container.
- Persists SQLite, PDF uploads, and generated internal secrets in `/data`.
- Uses the Home Assistant Supervisor Core API proxy for webhook delivery.
- Supports optional ntfy fallback.
- Builds locally on Home Assistant; no public container registry is required.
- Private GitHub Actions generates the self-contained install ZIP.
- Supports amd64 and aarch64.
