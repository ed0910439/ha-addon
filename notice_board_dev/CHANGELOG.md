# Changelog

## 2.6.3

- Repair the custom Tiptap Markdown tokenizer hook for existing PDF attachments.
- Reject malformed, incomplete or omitted PDF markers before replacing documents; preserve all original file IDs during Markdown source editing.
- Prevent switching modes or saving during in-flight PDF uploads to avoid losing newly uploaded files.
- Validate the canonical text length rather than raw Markdown delimiters, and restrict aria-pressed to stateful formatting controls.
- Compatibility hotfix while replacement with lakelib is evaluated.


## 2.6.2

- 將公告編輯器粗體、斜體、刪除線、標題、清單、引言、分隔線、復原／重做、PDF 改成有中文提示與 ARIA 名稱的圖示工具列。
- 新增「MD 排版」Switch：開啟可直接編輯 Markdown，關閉回到 Tiptap 視覺化排版；提交仍儲存原有 Tiptap JSON，不變更資料庫及舊公告格式。
- 在 Markdown 與富文字間轉換 PDF 附件時，以受保護標記及原始附件 ID 驗證；轉換有遺失疑慮則阻止儲存或切回，保留原始碼供修正。
- 為現有 jsDelivr Tiptap 模組的除錯 Source Map 指定範圍明確的 connect-src，避免開發者工具的連線被預設 CSP 阻擋；其餘 CSP 安全限制保留。
- 新增圖示／模式切換回歸測試，以及 JavaScript 語法檢查。


## 2.6.1

- 保留 2.6.0 公告總覽摘要卡與篩選，將下方列表更新為可展開公告；頁首顯示重要性、標題、發布者，頁腳顯示發布／到期時間與已閱名單。
- 一般短文展開後自動記錄已閱；長文捲到底或完整公告頁捲到結尾才記錄；嵌入 PDF 需明確確認，避免誤計。
- 依公告版本記錄已閱，單純 GET 不再自動計入；新版發布後獨立重計。
- 公告編輯器加入重要性與草稿保護欄位，既有 SQLite 記錄原樣保留。
- 增加外掛內的 Home Assistant 管理員選擇設定，從 Supervisor WebSocket 驗證當前管理員身分，保留本地帳戶作為復原入口。


## 2.6.0

**Manus AI 更新 — 2026-10-21.** Minor release: new dashboard interactions and settings features; existing authentication and announcement endpoints are retained.

- **Manus AI update:** Redesign the dashboard with a compact ProComponents-inspired layout, interactive summary filters, and responsive card groups.
- **Manus AI update:** Show only published announcements on the home page; published announcements with pending edits continue to show their current published version.
- **Manus AI update:** Add Settings navigation for account and trusted-device management, and require current-password re-verification before revoking a device.
- **Manus AI update:** Hide device logout controls in Home Assistant Ingress mode, add a footer with service and user-term links, and improve home-page navigation state.
- **Manus AI update:** Place the live unread indicator beside the Notice Board brand, hide redundant home navigation, and show a return-home link on other pages.
- **Manus AI update:** Respect hidden attributes for filtered cards and empty states so an empty-result box is never displayed alongside matching announcements.
- **Manus AI update:** Add explicit device-revocation confirmation, limit password re-verification failures, enforce device ownership, and route legacy logout requests to the verified device-management flow.
- **Manus AI update:** Add isolated regression tests for published content, navigation, footer links, Home Assistant presentation, CSRF protection, and device revocation.


## 2.5.5

- Refine the announcement board layout for tablet and mobile breakpoints.
- Improve touch target sizes, action-button wrapping, editor toolbar scrolling, and PDF preview sizing on narrow screens.
- Prevent long announcement titles, metadata, and rich-text content from causing horizontal overflow.


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
