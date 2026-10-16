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
6. 裡面會有 `notice-board-local-app-2.5.0.zip`。
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

## Home Assistant 側邊欄與 Ingress SSO

2.5.0 起 App 啟用 Home Assistant Ingress，安裝並啟動後可直接從 Home Assistant 左側看到：

```text
📢 公告
```

Ingress 由 Home Assistant 先完成使用者驗證。Notice Board 只在請求同時來自 Supervisor Ingress 來源，且帶有 Home Assistant 的使用者識別標頭時接受 SSO；公開網址上自行偽造相同 HTTP header 不會被信任。

第一次從 Ingress 進入時：

1. 若 `hass_user_id` 已經連結 Notice Board 帳號，直接使用該帳號。
2. 若 Home Assistant username 與一個尚未連結的 Notice Board 非 BOT 帳號相同，自動把兩者連結。
3. 若找不到可連結帳號，自動建立一個 `USER` 帳號。
4. Home Assistant 管理員身分不會自動換成 Notice Board `SYSADMIN`；Notice Board 權限仍由 Notice Board 帳號管理頁決定。

系統管理員可在「帳號管理」解除 Home Assistant 身分連結。自動建立的 Ingress 帳號使用未知隨機密碼，預設只能透過 Home Assistant 登入；若也需要從公開網址登入，可由 Notice Board 系統管理員重設一次性密碼，再完成原本的密碼與 TOTP 流程。

公開網址仍維持：

```text
https://notice.example.com
```

因此手機通知 deep link 不會依賴 Ingress 的暫時 session URL。

## 未讀狀態

Notice Board 以「目前使用者是否看過目前發布版本」作為唯一未讀依據：

- 看過 v1，但公告更新成 v2 → 重新變成未讀。
- 開啟 v2 → v2 記錄已讀，未讀數減少。
- 未讀狀態不以手機 Push 是否被點掉、也不以 Home Assistant 持久通知是否被關閉判定。

Notice Board 頂部的「公告」連結旁會在有未讀公告時顯示紅色圓點，並每 30 秒更新一次。Home Assistant 原生 App sidebar 目前沒有官方的動態 badge 欄位，因此 HASS 左側「公告」文字本身不會被修改；HASS 的通知中心則會顯示持久通知提示。

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

### 手機 Push 自動化範例

假設 `hass_webhook_id` 設成：

```text
notice_board_events
```

可建立一條 Home Assistant automation：

```yaml
alias: Notice Board - 手機推播
mode: queued
max: 50
triggers:
  - trigger: webhook
    webhook_id: notice_board_events
    allowed_methods:
      - POST
    local_only: true

conditions:
  - condition: template
    value_template: >
      {{ trigger.json is defined
         and trigger.json.recipient is defined
         and trigger.json.event in [
           'announcement.published',
           'announcement.updated',
           'announcement.withdrawn',
           'announcement.expired'
         ] }}

actions:
  - variables:
      notice_title: >
        {% if trigger.json.event == 'announcement.published' %}📢 新公告
        {% elif trigger.json.event == 'announcement.updated' %}📝 公告更新
        {% elif trigger.json.event == 'announcement.withdrawn' %}↩️ 公告已撤下
        {% else %}🕒 公告已到期
        {% endif %}
      notice_message: >
        {{ trigger.json.title }} · v{{ trigger.json.version }}

  - choose:
      - conditions: "{{ trigger.json.recipient == 'user_a' }}"
        sequence:
          - action: notify.mobile_app_USER_A_PHONE
            data:
              title: "{{ notice_title | trim }}"
              message: "{{ notice_message | trim }}"
              data:
                url: "{{ trigger.json.url }}"
                tag: "notice_board_{{ trigger.json.announcement_id }}"

      - conditions: "{{ trigger.json.recipient == 'user_b' }}"
        sequence:
          - action: notify.mobile_app_USER_B_PHONE
            data:
              title: "{{ notice_title | trim }}"
              message: "{{ notice_message | trim }}"
              data:
                url: "{{ trigger.json.url }}"
                tag: "notice_board_{{ trigger.json.announcement_id }}"
```

把範例中的 Notice Board username 與 `notify.mobile_app_...` 改成你實際的帳號／裝置即可。固定的 `tag` 讓同一公告更新時可覆蓋同一組手機通知，而不是一直堆新通知。

## Home Assistant 持久通知

2.5.0 預設開啟：

```yaml
hass_persistent_notifications: true
```

Notice Board Gateway 會直接使用 App 的 `SUPERVISOR_TOKEN` 呼叫 Home Assistant Core API，不需要 Long-Lived Access Token。

行為：

- `announcement.published` → 建立「📢 新公告」持久通知。
- `announcement.updated` → 使用相同 `notification_id` 更新該公告的持久通知。
- `announcement.withdrawn` → 移除該公告持久通知。
- `announcement.expired` → 移除該公告持久通知。

固定 ID 格式：

```text
notice_board_<announcement_id>
```

Persistent Notification 是 Home Assistant 全域通知，不是每位使用者各自一份。因此真正的個人未讀狀態仍以 Notice Board 的 read receipt 為準。

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
