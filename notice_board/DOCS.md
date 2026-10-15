# Notice Board Home Assistant Local App

## 架構

此版本專門給私人 Home Assistant 使用，不需要公開 GitHub repository，也不需要公開 GHCR image。

Home Assistant 會在本機建置一個 App container，container 內由 s6 監督兩個獨立 process：

1. Notice Board：port `8000`
2. Notification Gateway：loopback `127.0.0.1:8090`

Gateway 不會暴露成 Home Assistant host port。

持久資料都在 Home Assistant 管理的 `/data`：

- `/data/notice.db`
- `/data/uploads/`
- `/data/.noticeboard-secrets`

APP secret 與內部 Gateway shared secret 會在第一次啟動時自動產生，之後跟著 App data 備份與還原。

## 取得私人安裝包

Notice Board 主程式 repository 保持 Private。

每次 GitHub Actions 的 **Home Assistant Local App** workflow 成功後，會產生一個只有有權限存取此 private repository 的帳號才能下載的 artifact。

在 GitHub：

1. 開啟 `Notice-Board` repository。
2. 進入 **Actions**。
3. 開啟最新成功的 **Home Assistant Local App** run。
4. 在 **Artifacts** 下載 `notice-board-local-app-<commit sha>`。
5. 解壓第一次下載的 artifact ZIP。
6. 裡面會有 `notice-board-local-app-2.4.0.zip`。
7. 再解壓後會得到完整的 `notice_board/` 資料夾。

## 安裝到 Home Assistant

Home Assistant 目前的本機 App 目錄是：

```text
/local_apps
```

可透過 Home Assistant 的 SSH 或 Samba App 把整個資料夾複製成：

```text
/local_apps/notice_board/
├── config.yaml
├── Dockerfile
├── requirements.txt
├── app/
├── notification_gateway/
├── ha_addon/
├── translations/
├── DOCS.md
├── README.md
└── CHANGELOG.md
```

不要只複製 `config.yaml`，這個 Local App 是由 Supervisor 在 HASS 主機上從完整資料夾本地 build。

完成複製後，在 Home Assistant 的 Apps / App Store 重新載入本機 Apps。看到 **Notice Board** 後即可安裝。

此 Local App 的 `config.yaml` 刻意沒有 `image:`，因此 Supervisor 會本地建置，不會嘗試從公開 container registry 拉 image。

## 第一次設定

安裝後先開 **Configuration**。

至少設定：

- `bootstrap_admin_password`：第一次登入的暫時密碼，至少 12 碼。
- `public_url`：手機通知真正要打開的固定 HTTPS 網址，例如 `https://notice.example.com`。

初始帳號預設是：

```text
admin
```

當資料庫已經存在使用者後，bootstrap 密碼不會再拿來重新建立帳號。

## 對外網址

App 預設把 Notice Board 的 container port `8000` 映射到 Home Assistant host port：

```text
8088
```

區網測試可以先開：

```text
http://HOME_ASSISTANT_IP:8088
```

正式通知連結建議使用獨立 HTTPS hostname，例如：

```text
https://notice.example.com
```

再由你現有的 Cloudflare Tunnel / reverse proxy 指向 Home Assistant 主機的 `8088`。

Ingress 不作為公告的 canonical URL，因為公告網址需要能直接放進手機通知、保持固定 deep link，並維持「未登入 -> 登入/2FA -> 回原公告」流程。

## Home Assistant webhook

在 Configuration 填入：

```text
hass_webhook_id
```

只需要填 webhook ID，不需要填完整 HASS URL，也不需要建立 Home Assistant long-lived access token。

Gateway 會在 App 內呼叫：

```text
http://supervisor/core/api/webhook/<hass_webhook_id>
```

並使用 Home Assistant 啟動 App 時提供的 `SUPERVISOR_TOKEN` 作為 bearer token。

支援事件：

- `announcement.published`
- `announcement.updated`
- `announcement.withdrawn`
- `announcement.expired`

Home Assistant automation 可依 payload 裡的 `recipient` 對應至各自的 `notify.mobile_app_*`。

## ntfy fallback

如果 HASS webhook timeout 或回傳失敗，Gateway 可送 ntfy。

可選設定：

- `ntfy_default_topic`
- `ntfy_topic_map`
- `ntfy_access_token`
- 或 `ntfy_username` + `ntfy_password`

`ntfy_topic_map` 範例：

```json
{"user_a":"noticeboard-user-a-long-random-topic"}
```

若 HASS webhook 與 ntfy target 都沒有設定，Notice Board 會自動停用 outbound notification delivery。

## Cloudflare

只有在所有 Notice Board 公開流量都確實經過可信任的 Cloudflare proxy/tunnel 時才開啟：

```text
trust_cloudflare
```

Cookie security 會依 `public_url` 自動決定：

- `https://...` -> Secure cookie
- 沒有 HTTPS public URL -> 允許本機 HTTP 測試

## 備份

此 App 使用：

```yaml
backup: cold
```

Home Assistant 在備份 App 前會先停止它，讓 SQLite 與 PDF 在一致狀態下被保存。

還原 App backup 時會一起還原：

- SQLite database
- PDF uploads
- APP secret
- internal Gateway secret

## 更新私人 Local App

1. 等 private GitHub Actions 產生新版 artifact。
2. 下載並解壓新版 `notice_board/`。
3. 停止 Notice Board App。
4. 用新版程式檔覆蓋 `/local_apps/notice_board/`。
5. 不要刪除 Home Assistant 管理的 App `/data`。
6. 在 App Store 重新載入本機 Apps。
7. 當 `config.yaml` 的 version 增加後，重新建置／更新 Notice Board。
8. 啟動後檢查 Log 與 `/healthz`。

程式包與資料是分離的，因此更新 `/local_apps/notice_board` 不會覆蓋 `notice.db` 或 PDF。
