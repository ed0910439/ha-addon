# Changelog

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
