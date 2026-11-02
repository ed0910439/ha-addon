# Notice Board (DEV) — LakeJS test app

This is an **isolated Home Assistant app** for testing LakeJS. It is **not** an upgrade or replacement for the production `notice_board` app.

## Code origin

- Baseline: production **Notice Board 2.6.3** (`master`), including the PDF hotfix.
- Lake editor: experimental implementation from [PR #5](https://github.com/ed0910439/ha-addon/pull/5) (`feature/lakelib-richtext-safe-migration`).
- Test app slug: `notice_board_dev`; display name: **Notice Board (DEV)**.
- Version: `2.6.3-dev.1`.

## Installation and data separation

1. In Home Assistant, **Settings → Apps → Install app**, reload the existing repository `https://github.com/ed0910439/ha-addon`.
2. Install **Notice Board (DEV)** separately from **Notice Board**.
3. Set up a test admin login (do **not** reuse production credentials). Start the test app and select **Open Web UI** / its Ingress panel.
4. Use **only dummy announcements and PDF files**. Do not migrate or restore production data here.

Its different `slug` gives it a separate Supervisor app instance and persistent `/data` volume. Ingress remains enabled, sidebar access is admin-only; startup is manual. External host port mapping is **disabled by default**. Home Assistant persistent notifications are **disabled by default**; **do not** configure production ntfy topics, webhook IDs, or external public URLs in the test app.

## What is currently testable

- LakeJS 0.4.6 loads on announcement edit pages, with a Tiptap JSON adapter.
- Supported text: paragraphs, headings, lists, quotes, bold, italic, strike, code, horizontal rules.
- Existing PDF documents and unsupported marks automatically stay on Tiptap.
- Adding a PDF from a Lake document switches to Tiptap to retain attachment support.
- Markdown shortcuts exist; **full Markdown raw-source mode is not a Lake feature implemented here**.
- Lake CSS and JS come from jsDelivr CDN; network access is required to load them. The legacy editor remains available as a fallback.
- The test version is **not validated for production use**. Verify round trips and PDF upload races first.

## Tests

From this app directory in a development environment:

```sh
python -m pip install -r requirements.txt httpx
python -m unittest discover -s tests -v
npm install --no-save --no-package-lock linkedom@0.18.12 playwright-core@1.56.1
node --input-type=module --check < app/static/editor.js
node --input-type=module --check < app/static/lake_adapter.js
node --input-type=module --check < app/static/lake_editor.js
node tests/test_lake_adapter.mjs
node tests/test_lake_browser.mjs
```

The browser test needs Chromium/Chrome on PATH. All test actions must target test data only.
