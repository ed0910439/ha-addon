# Notice Board — Home Assistant App

## Installation

In Home Assistant:

1. Open **Settings → Apps → App Store**.
2. Open **⋮ → Repositories**.
3. Add:

```text
https://github.com/ed0910439/ha-addon
```

4. Refresh the App Store.
5. Install **Notice Board 2.4.0**.

Supervisor downloads the App source from this repository and builds it locally. No manual ZIP extraction is required.

## First configuration

Before first start, set at least:

- `bootstrap_admin_password`: temporary password for the first administrator login, at least 12 characters.
- `public_url`: stable HTTPS URL placed in notification links, for example `https://notice.example.com`.

Default bootstrap username:

```text
admin
```

## Web access

The App exposes Notice Board on Home Assistant host port `8088`.

Local test:

```text
http://HOME_ASSISTANT_IP:8088
```

For normal use, route a dedicated HTTPS hostname through Cloudflare Tunnel or your reverse proxy to Home Assistant port `8088`, then set the same address as `public_url`.

## Home Assistant webhook delivery

Set `hass_webhook_id` to the webhook ID of the Home Assistant automation receiving Notice Board events.

The internal Notification Gateway calls Home Assistant through the Supervisor Core API proxy:

```text
http://supervisor/core/api/webhook/<hass_webhook_id>
```

Authentication uses the App runtime `SUPERVISOR_TOKEN`; a long-lived Home Assistant access token is not required.

Supported events:

- `announcement.published`
- `announcement.updated`
- `announcement.withdrawn`
- `announcement.expired`

## ntfy fallback

If the Home Assistant webhook fails or times out, Gateway can fall back to ntfy.

Useful options:

- `ntfy_default_topic`
- `ntfy_topic_map`
- `ntfy_access_token`
- or `ntfy_username` + `ntfy_password`

Example topic map:

```json
{"user_a":"noticeboard-user-a-long-random-topic"}
```

## Data and backups

Persistent files are stored in Home Assistant-managed `/data`:

- SQLite database;
- PDF uploads;
- generated APP secret;
- generated internal Gateway secret.

The App uses `backup: cold`, so Home Assistant stops it before an App backup for a more consistent SQLite/PDF snapshot.
