# STAGE — 全片共用舞台規格（每個 frame worker 必讀）

這部片是 Lecture Live Notes 的架構解說。全片共用一張架構圖。每一幀都必須照下面的座標畫，
觀眾才會覺得是同一張圖在逐層展開，而不是十張不相干的投影片。

畫布 1920×1080。下表座標以 px 寫，實作時換成 `cqw`/`cqh`（frame.md 的 container law：px ÷ 1920 × 100 = cqw）。

## 1. 字型（全片唯一允許的 @font-face）

每個 frame 檔案內都要放這段 `@font-face`（路徑相對於 frame 檔 `compositions/frames/NN-*.html`）：

```css
@font-face { font-family: "Newsreader";     src: url("../../assets/fonts/Newsreader-400.woff2") format("woff2"); font-weight: 100 900; }
@font-face { font-family: "Hanken Grotesk"; src: url("../../assets/fonts/HankenGrotesk-400.woff2") format("woff2"); font-weight: 100 549; }
@font-face { font-family: "Hanken Grotesk"; src: url("../../assets/fonts/HankenGrotesk-600.woff2") format("woff2"); font-weight: 550 900; }
@font-face { font-family: "DM Mono";        src: url("../../assets/fonts/DMMono-400.woff2") format("woff2"); font-weight: 100 900; }
@font-face { font-family: "Noto Serif TC";  src: url("../../assets/fonts/NotoSerifTC-600.woff2") format("woff2"); font-weight: 100 900; }
@font-face { font-family: "Noto Sans TC";   src: url("../../assets/fonts/NotoSansTC-500.woff2") format("woff2"); font-weight: 100 599; }
@font-face { font-family: "Noto Sans TC";   src: url("../../assets/fonts/NotoSansTC-700.woff2") format("woff2"); font-weight: 600 900; }
```

字族堆疊（拉丁字落在西文字型，中文字落在 Noto TC）：

| 角色 | font-family | 用途 |
|---|---|---|
| display | `"Newsreader", "Noto Serif TC", serif` | 標題、層名、大字、收尾句 |
| body | `"Hanken Grotesk", "Noto Sans TC", sans-serif` | 需求／設計說明、方塊名稱 |
| mono | `"DM Mono", "Noto Sans TC", monospace` | 檔名、常數、LAYER 編號、數字標籤 |

- 中文標題不要加負字距（frame.md 的 negative tracking 只用在拉丁字）；中文行高 1.25 以上。
- 中文字形只涵蓋 `STORYBOARD.md` 裡出現過的字。**畫面上的中文只能用 STORYBOARD.md 出現過的字**；
  需要新字時照寫，orchestrator 會重跑 `tools/fetch_fonts.py` 補字形。
- 不要用 emoji、不要用 ✍️、⟲ 這類符號字形（字型裡沒有）；需要圖示時用 inline SVG 畫。
- 任何承載資訊的文字 ≥ 1.4cqw（約 27px）。

## 2. 顏色

只用 frame.md 的兩色：paper `#F0EBDE` 底、cobalt `#1F2BE0` 墨。沒有紅色、沒有綠色。

- **「亮起／被選中」的方塊** ＝ cobalt 實心填滿 + paper 色文字。
- **一般方塊** ＝ paper 底 + 0.12cqw cobalt 外框 + cobalt 文字。
- **未聚焦方塊** ＝ 外框與文字用 `ink-faint`（18% cobalt）。
- **失敗／不允許** ＝ cobalt 斜線刪除（兩條交叉的 0.16cqw 線）或 cobalt 刪除線，絕不用紅色。
- 0 圓角、0 陰影、0 漸層。

## 3. 標準架構圖（Frame 2 與 Frame 10 的全尺寸版）

| 方塊 id | 位置 (x1–x2, y1–y2) | 主標（body 600） | 副標（mono） |
|---|---|---|---|
| `browser` | 120–440, 420–560 | 瀏覽器／手機 | PWA · 16kHz PCM |
| `server` | 680–1060, 400–580 | FastAPI 伺服器 | app.py · /ws |
| `disk` | 720–1020, 680–800 | 磁碟 | lectures/YYYYMMDD/HHMMSS/ |
| `live` | 1340–1760, 240–380 | Gemini Live（即時語音辨識） | WebSocket |
| `flash` | 1340–1760, 600–740 | Gemini Flash（筆記・章節・整併・翻譯） | streamGenerateContent |

- 虛線框「你的電腦」：x 580–1160、y 300–860，cobalt 虛線（dash 長約 0.6cqw），左上角放 mono 標籤 `你的電腦`。
- 右欄上方 y≈200 放 mono 標籤 `雲端`，置中於 x 1550。
- 連線（cobalt 實線 0.12cqw，箭頭為小三角形）：
  - `browser → server`：水平線 y=490，線上方 mono 標籤 `/ws 音訊`。
  - `server ⇄ live`：從 server 右緣 (1060, 450) 折到 live 左緣 (1340, 310)，標籤 `音訊 → 逐字稿`。
  - `server ⇄ flash`：從 server 右緣 (1060, 530) 折到 flash 左緣 (1340, 670)，標籤 `prompt → 筆記`。
  - `server → disk`：垂直線 x=870，從 y=580 到 y=680。
- 這張圖的外接範圍約 x 120–1760、y 200–860，所有內容都在底部 83%（y ≤ 896）以上。

## 4. 架構小地圖（Frame 3–9 共用，固定在右上角）

- 位置：外框 x 1440–1840、y 96–266（400×170），paper 底、0.12cqw cobalt 外框，外框上緣左側 mono 標籤 `架構位置`。
- 內容：上面那張標準架構圖等比縮小（約 0.22 倍），只畫五個方塊（不寫副標）、虛線框與連線；方塊內只寫短名：
  `瀏覽器`、`FastAPI`、`磁碟`、`Live`、`Flash`。
- 本層相關的方塊與連線用「亮起」樣式（cobalt 實心），其他用 `ink-faint`。哪些要亮起寫在各幀的 `minimap:` 欄位。
- 小地圖在幀開頭 0.0–0.6s 內就位（淡入 + 輕微上移，power3），之後完全靜止。亮起的方塊在層名出現後才亮。

## 5. 層幀版型（Frame 3–9 共用的舞台）

- **頂欄**（左上）：mono `LAYER 0N / 07` 在上，display 層名在下（headline 級，約 4.6cqw），
  底下一條 0.12cqw cobalt topbar-rule，從 x 80 延伸到 x 1400（停在小地圖左邊）。y 範圍約 96–270。
- **左欄**（x 80–620，y 320–880）：兩張堆疊的卡片。
  - 「需求」卡：Hanken 600 大寫風格的 micro-strong 標籤 `需求`，下面一句 body-lede 級中文（≥1.5cqw）。
  - 「設計」卡：標籤 `設計`，下面一句設計摘要，再下面一行 mono 的程式出處（常數名／函式名／ADR 編號）。
  - 兩張卡片之間一條 ink-faint 分隔線。需求卡在旁白講到需求時出現，設計卡在旁白說「所以」時出現。
- **主舞台**（x 700–1840，y 320–880）：本層的機制圖，是這一幀的 focal，佔畫面 40–60%。
- 層幀都不放 pixel-glitch 與 QR-block（那是宣告型幀的裝飾，只出現在 Frame 1、2、10）。
- page chrome：右下 mono `0N / 10`，左下 mono `LECTURE LIVE NOTES · ARCHITECTURE`，都在底部 hairline 上方。

## 6. 動態語法

- 一律 power3 長尾減速，不彈跳、不 overshoot。
- 每個元素在旁白講到它時才出現（各幀的 Scene 時間已對齊旁白）。開頭只出現頂欄與小地圖。
- 內容到齊後靜止。不呼吸、不慢推、不漂移。
- 所有進場用 `fromTo`，GSAP 單一 paused timeline，無 `Math.random`、無 `repeat: -1`。
