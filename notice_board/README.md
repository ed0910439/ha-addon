# Notice Board — Home Assistant Local App

這是 Notice Board 的私人 Home Assistant App 包裝。

特性：

- Notice Board 與 Notification Gateway 在同一個 App container 中以不同 process 執行。
- SQLite、PDF 與內部 secrets 全部放在 Home Assistant 管理的 `/data`。
- `backup: cold`，備份時由 Supervisor 先停止 App。
- 直接使用 Supervisor Core API proxy 把公告事件送回 Home Assistant。
- Notification Gateway 保留 ntfy fallback。
- 不需要公開 GitHub repository。
- 不需要公開 Docker / GHCR image。
- Supervisor 會在 HASS 主機上本地 build。

安裝包由 private GitHub Actions 產生。完整步驟請看 **Documentation**。
