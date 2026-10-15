# Changelog

## 2.4.1

- System administrators can now create and manage their own announcements.
- Administrators still cannot edit announcements created by other users.
- Administrator-owned drafts, pending updates, and unpublished PDF attachments are handled as owner content.

## 2.4.0

- Initial Home Assistant App repository release.
- Notice Board and Notification Gateway run as separate supervised processes.
- SQLite, PDF uploads and generated secrets persist in `/data`.
- Home Assistant webhook delivery uses the Supervisor Core API proxy.
- Optional ntfy fallback.
- Supports amd64 and aarch64.
