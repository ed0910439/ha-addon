# Changelog

## 2.4.0

- Initial private Home Assistant Local App package.
- Runs Notice Board and Notification Gateway as separate supervised processes in one container.
- Persists SQLite, PDF uploads, and generated internal secrets in `/data`.
- Uses the Home Assistant Supervisor Core API proxy for webhook delivery.
- Supports optional ntfy fallback.
- Builds locally on Home Assistant; no public container registry is required.
- Private GitHub Actions generates the self-contained install ZIP.
- Supports amd64 and aarch64.
