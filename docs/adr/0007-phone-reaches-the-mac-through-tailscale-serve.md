# 7. 手機透過 Tailscale Serve 連回 Mac，前端做成 PWA

## Status

Accepted

## Context

到目前為止，錄音一定得在跑 server 的那台電腦上進行。可是上課時手邊最方便的是手機（Android）：
收音位置可以往講台靠，也不必為了錄音把筆電打開一整堂課。

要讓手機用這個 App，得先解決三個限制：

1. **麥克風只能在 secure context 裡開。** `getUserMedia` 只在 HTTPS 或 localhost 上可用。
   手機連 `http://192.168.x.x:8000` 時，瀏覽器會直接拒絕要麥克風。
2. **server 沒有任何驗證。** 它一直只聽 `127.0.0.1`，所以不需要驗證。只要改聽 `0.0.0.0`，
   同一個 Wi-Fi 上的任何人都能用這台機器上的 Gemini／Anthropic key，也能下載課堂內容。
   偏偏上課用的正是學校 Wi-Fi 這種共用網路。
3. **學校 Wi-Fi 常開 client isolation。** 就算手機跟 Mac 在同一個網路，也可能互相連不到。

評估過三種做法：

1. **mkcert + 區網 + token**：自簽 CA，把根憑證裝進手機並開啟信任；server 改聽 `0.0.0.0`，
   每個端點（包含 `/ws` 與 `/download`）都要加 token 驗證。這解決了 1 跟 2，解決不了 3，
   而且驗證要自己寫、自己維護。
2. **部署到雲端**：跟「全部跑在使用者自己的機器上」的前提衝突，API key 和課堂內容都得放上
   別人的主機，驗證同樣要自己寫。
3. **Tailscale Serve**：Mac 與手機都加入同一個 tailnet，`tailscale serve` 用 `*.ts.net`
   的正式憑證提供 HTTPS，轉給 `127.0.0.1:8000`。

## Decision

採用 Tailscale Serve。server 維持只聽 `127.0.0.1`，後端不改。

選它是因為三個限制它一次全解決，而且都不是靠我們自己寫的程式碼：憑證是 Let's Encrypt
簽的，手機不用裝 CA；連得到的只有 tailnet 裡的裝置，存取控制交給 Tailscale 的 ACL；
連線走 WireGuard，client isolation 擋不住，Mac 放在家裡也連得到。

前端做成 PWA，安裝到 Android 主畫面，打開就是全螢幕，沒有網址列：

- `manifest.webmanifest` 與 192／512／maskable 圖示。圖示由 `scripts/icon.svg` 產生，
  用的是 `scripts/build_pwa_icons.py`，產出的 PNG 提交進 repo。
- service worker **只攔截頁面導覽**。server 回得了就一律用 server 的回應；連不上
  （Mac 睡著、server 沒開、Tailscale 斷線）才改回一張預先快取的 `offline.html`，
  告訴使用者問題出在哪一端。`app.js`、`styles.css`、API、`/download`、`/bundle` 都不經過
  快取。這個 App 的一切都要靠 server 才能做，快取頁面外殼只會製造「看起來能用、按下去才失敗」
  的狀況，還會讓舊版 `app.js` 卡在手機上。
- 錄音期間持有 Screen Wake Lock。手機螢幕自動關閉時瀏覽器可能暫停頁面，收音就會中斷。
  頁面切到背景時瀏覽器會自動釋放鎖，回到前景時要重新取得。

## Consequences

多了一個外部依賴。Mac 和手機都得安裝並登入 Tailscale，tailnet 也要在管理後台開啟
MagicDNS 與 HTTPS Certificates。Tailscale 帳號出問題時，手機就不能用了（Mac 本機照常）。

手機上的網址是 `https://<mac 名稱>.<tailnet>.ts.net`，不是 `127.0.0.1:8000`。PWA 綁的是
origin，所以 Mac 改名或換 tailnet 後得重新安裝。`tailscale serve` 也固定轉到 8000，
launcher 拿不到 8000 而改用隨機 port 時（見 #3），手機就連不到。

2026-09-29 在 Android 實機上驗證過：經由 `tailscale serve` 安裝 PWA、錄音、逐字稿、
下課整併都正常。那次測試沒有量頻寬，也沒有確認連線是直連還是走 DERP relay。音訊是
16 kHz 16-bit mono，約 32 KB/s，走 relay 應該也撐得住，但這只是推估。自動化測試仍然只跑
localhost。

Mac 必須從上課一路醒到整併完成。server、最終整併、背景重試都在 Mac 上，所有檔案也存在
Mac 上，手機上一個都沒有。MacBook 闔上螢幕就會睡著，就算接著電源也一樣。

手機網路一抖，這堂課就結束了。現行設計下 WebSocket 斷線等於下課：server 會用已經收到的內容
收尾整併，前端顯示「連線已中斷」。手機在 Wi-Fi 與行動網路之間切換、或 Tailscale 重新連線，
都會觸發這個狀況。要支援斷線續錄，得改 session 模型，這不在這次的範圍。

Wake Lock 只能讓螢幕不關，擋不住使用者自己切到別的 App。Android Chrome 在背景是否持續收音，
沒有實測過；README 請使用者錄音時讓 App 留在前景。

service worker 只快取一張 `offline.html`。更新提示頁內容時要改 `sw.js` 裡的快取名稱，
不然已安裝的手機會一直顯示舊版提示頁。
