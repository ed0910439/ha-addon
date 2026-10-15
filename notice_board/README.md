# Notice Board

Notice Board is distributed through this Home Assistant App repository.

## Install

Add this repository in Home Assistant:

```text
https://github.com/ed0910439/ha-addon
```

Then go to **Settings → Apps → App Store**, refresh the store, and install **Notice Board**.

The App provides:

- trusted-device login and TOTP 2FA;
- announcement publishing, scheduling and expiry;
- per-version read receipts and personalized diffs;
- Tiptap rich-text editing;
- authenticated PDF attachments;
- Home Assistant notification delivery with ntfy fallback.

Notice Board and Notification Gateway run as separate supervised processes inside the same App container. Persistent database, PDF uploads and generated internal secrets live in Home Assistant-managed `/data`.
