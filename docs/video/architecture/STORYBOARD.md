---
format: 1920x1080
duration: 140s
message: "Lecture Live Notes 的每一個架構設計，都對應一個上課時真的會遇到的需求或失敗"
arc: concept-explainer with process
audience: "懂一點程式的開發者：專案維護者本人、未來的協作者"
mode: autonomous
music: none  # host BGM: assets/audio/bgm_bed.wav (tools/build_bed.py, MiniMax Music 3 seed 7 slice, pre-ducked)
---

## Video direction

- **舞台**：全片共用一張架構圖，規格在 `STAGE.md`（座標、小地圖、層幀版型、字型、兩色規則）。Frame 2 與 Frame 10 是全尺寸架構圖；Frame 3–9 是層幀：左上頂欄、左欄「需求／設計」兩張卡、右上架構小地圖、右下主舞台機制圖。
- **調色**：frame.md 的 cobalt-grid，只有 paper `#F0EBDE` 與 cobalt `#1F2BE0`。亮起＝cobalt 實心；失敗＝cobalt 斜線刪除；永遠不出現第二種色相。方格紙網格與上下 hairline 每幀都在。
- **字型**：display `Newsreader → Noto Serif TC`、body `Hanken Grotesk → Noto Sans TC`、mono `DM Mono → Noto Sans TC`，全部是 `assets/fonts/` 的本地檔。
- **動態語法**：power3 長尾減速；每個元素在旁白講到它的時候才出現（各 Scene 的秒數已對齊實際旁白）；到齊後靜止，靜止期最多 subtle jitter（多數幀連 jitter 都不要）。
- **節奏**：Frame 1 動得最多（kinetic type）；Frame 2 是組裝；Frame 3–9 是同一個版型的七次變奏（push-slide LEFT 串接，像翻頁）；Frame 5 是全片唯一的數據幀；Frame 10 回到全貌後長時間靜止，是全片的呼吸點。
- **不要**：一開場就把整幀倒滿再凍住（slideshow）；元素各自漂浮（screensaver）；呼吸縮放；後半段慢推慢搖；彈跳；emoji；紅綠色；圓角；陰影；漸層；任何真實課堂內容（`lectures/` 裡的東西）。
- **字幕**：不燒字幕（畫面本身就是架構文字），另外輸出 SRT。底部 17% 仍保持乾淨。

## Frame 1 — 下課就有筆記

- scene: 一行字「一堂課 2 小時」，旁邊手抄筆記的行一條條斷掉、留下空白；空白被一份完整的筆記卡片填滿
- voiceover: "一堂課兩小時，邊聽邊抄，一定會漏。如果你只管專心聽課，下課那一刻，一份能複習的中文筆記，就已經寫好了呢？"
- duration: 10s
- transition_in: cut
- status: animated
- src: compositions/frames/01-hook.html
- type: hook
- persuasion: Pain validation + Imagine / scenario
- beat: recognition → intrigue
- blueprint: kinetic-type-beats

narrativeRole: 用觀眾自己的經驗打開缺口：手抄一定漏，然後丟出「下課就有筆記」的承諾。
keyMessage: 這個 App 要解決的是「聽課跟記筆記不能同時做好」。

- blueprint: kinetic-type-beats (Adapt)
- focal: 「一堂課 2 小時」的大字與它底下斷掉的手抄筆記線
- roles: 大字 display-hero = foreground subject · 手抄筆記線（6 條 ruled strip）= supporting · 方格紙網格 + pixel-glitch 欄（右側）+ QR-block（右上）= background · 最後的 final_notes.md 卡片 = foreground（接手 focal）
- rules: dynamic-content-sequencing, scale-swap-transition

Adapt：保留 kinetic-type-beats 的「一句話分多拍、每拍一個動作、收在定格」；把最後的 logo payoff 換成一張已寫好的筆記卡片。
Scene 1 (0.6–2.2s): 左側 1/3 高度出現 display-hero「一堂課 2 小時」，per-word reveal（「一堂課」→「2 小時」兩拍），右側 pixel-glitch 欄同時以 stair-step 方式長出。Rule-of-thirds，左錨。
Scene 2 (2.2–3.9s): 「邊聽邊抄」時，大字下方 6 條手抄筆記線從左往右逐條畫出（body 文字用「— — —」狀的抽象筆畫，不寫真實內容）；「一定會漏」時其中 3 條線中段被切出空白缺口（缺口處 mono 小字 `漏`），缺口一個接一個出現。
Scene 3 (4.0–6.4s): 「如果你只管專心聽課」：大字與筆記線用 scale-swap 縮小淡出到左上角（變成 mono 註腳 `09:00 → 11:00`），中央換上 display-chapter「只管聽課」。
Scene 4 (6.5–10.0s): 「一份能複習的中文筆記」：右側 60% 區域一張筆記卡片組起來——mono 檔名 `final_notes.md` 頂欄，下面 4 列 ledger-row（`章節 1`…`章節 3`、`我的課堂筆記（原文）`），列由上往下 stagger 填入；「已經寫好了」時卡片右上角蓋一個 cobalt 實心方章 `下課`。之後完全靜止到幀尾。

## Frame 2 — 五個方塊

- scene: 整張架構圖在方格紙上組起來：瀏覽器／手機 → FastAPI → Gemini Live、Gemini Flash，FastAPI 下方是磁碟；虛線框標「你的電腦」
- voiceover: "這就是 Lecture Live Notes。server 跑在你自己的電腦上，架構只有五個方塊。而每一個設計，都是被一個真實的需求，或一次真實的失敗，逼出來的。"
- duration: 10.75s
- transition_in: crossfade
- status: animated
- src: compositions/frames/02-overview.html
- type: product_intro
- persuasion: Frame-then-fill（先給全貌，之後逐層填）
- beat: orientation + anticipation

narrativeRole: 建立全片的舞台（架構圖），並在第二拍就說出論點：每個設計都對應一個需求或失敗。
keyMessage: 五個方塊；之後每一層都是「需求 → 設計」。

- blueprint: compose
- minimap: （本幀就是全尺寸架構圖，不放小地圖）
- focal: STAGE.md §3 的全尺寸架構圖
- roles: 五個方塊 + 連線 = foreground subject · 「你的電腦」虛線框 + `雲端` 標籤 = supporting · 底部論點句 = foreground（後段接手）· 網格 + 左側低透明 pixel-glitch = background
- rules: svg-path-draw, spring-pop-entrance, dynamic-content-sequencing

Scene 1 (0.0–1.4s): 左上 Hanken 標籤 `ARCHITECTURE` + display「Lecture Live Notes」（table-name 級，約 2.4cqw）淡入上移。畫面其餘只有網格。
Scene 2 (1.4–3.4s): 「server 跑在你自己的電腦上」：「你的電腦」虛線框 svg-path-draw 畫出，接著 `server`（FastAPI 伺服器）與 `disk` 兩個方塊在框內依序淡入（power3，從 8% 下方升起），`server → disk` 連線畫出。
Scene 3 (3.5–5.0s): 「架構只有五個方塊」：`browser`、`live`、`flash` 依序進場（每個間隔約 0.25s），三條連線依序 svg-path-draw；`雲端` 標籤出現。右上角 mono 計數 `5 個方塊` 在第五個方塊落定時出現。
Scene 4 (5.1–10.75s): 架構圖不動。圖下方（y≈900 以上的空間不夠時，把架構圖整體上移到 y 160 起）出現一行 display 論點句，per-phrase reveal：「每一個設計，都對應」(5.1s) →「一個真實的需求」(6.6s) →「或一次真實的失敗」(7.9s)；「需求」「失敗」兩詞用 css-marker 底線強調（cobalt 實心底線從左畫到右）。之後靜止。

## Frame 3 — 第一層：資料流

- scene: 角落小地圖亮起整條管線；主畫面是一條壓縮管線：聲波 16kHz → 逐字稿 → 每 60 秒一段筆記 → 每 10 段一章 → 下課整併
- voiceover: "先看資料怎麼流。需求是：還在上課，就要有筆記。所以聲音以十六千赫串流進來，即時變成逐字稿；每六十秒，寫成一段筆記；每十段，再壓成一個章節。下課時，只要整併這些章節就好。"
- duration: 14.86s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/03-pipeline.html
- type: feature_showcase
- persuasion: Causal chain + Progressive disclosure
- beat: comprehension
- need: 邊上課邊要有筆記；一整堂課太長，不能等到最後一次餵給模型
- design: 分層壓縮：逐字稿 → NOTE_WINDOW_SECONDS 60s 一段 → ROLLUP_EVERY_BLOCKS 10 段一章 → 最終整併

narrativeRole: 第一個需求→設計對：即時性靠分層壓縮達成。
keyMessage: 筆記是一路壓縮出來的，下課只剩最後一步。

- blueprint: compose
- minimap: 亮起 browser、server、live、flash 與 browser→server、server⇄live、server⇄flash 三條連線
- focal: 主舞台的五段壓縮管線（水平 full-width strip）
- roles: 管線五個站點 = foreground subject · 需求／設計卡 = supporting · 頂欄 + 小地圖 = supporting · 網格 = background
- rules: dynamic-content-sequencing, stat-bars-and-fills, scale-swap-transition

管線是主舞台裡一條水平的五站 strip（站點等寬、站間有箭頭）：① `16kHz PCM`（聲波圖示）② `逐字稿` ③ `每 60 秒 → 一段筆記` ④ `每 10 段 → 一章` ⑤ `下課 → 最終整併`。站點下方 mono 註記程式出處：② `Gemini Live` ③ `NOTE_WINDOW_SECONDS = 60` ④ `ROLLUP_EVERY_BLOCKS = 10` ⑤ `final_prompt`。
Scene 1 (0.0–1.5s): 頂欄 `LAYER 01 / 07` +「資料流」與小地圖就位；層名出現後小地圖的管線方塊亮起。
Scene 2 (1.6–4.2s): 需求卡出現：「還在上課，就要有筆記。」
Scene 3 (4.3–6.3s): 設計卡出現：「分層壓縮：逐字稿 → 段落 → 章節 → 整併」+ mono `NOTE_WINDOW_SECONDS · ROLLUP_EVERY_BLOCKS`。同時管線站 ① 進場，聲波 SVG 畫出（svg-path-draw）。
Scene 4 (6.4–7.6s): 站 ② `逐字稿` 進場，站內三行抽象文字線從左往右流入。
Scene 5 (7.7–9.6s): 站 ③ 進場，站內方格（pixel cells）一格一格點亮，代表一段一段筆記。
Scene 6 (9.7–11.5s): 站 ④ 進場：10 個小方格 scale-swap 收合成 1 個較大的實心方塊（「一章」）。
Scene 7 (11.6–14.86s): 站 ⑤ 進場，章節方塊們匯成一張 `final_notes.md` 卡片；站 ⑤ 外框加粗成實心 cobalt。之後靜止。

## Frame 4 — 第二層：連線

- scene: 小地圖亮 FastAPI⇄Gemini Live 與磁碟；主畫面是一條時間軸，連線段在 570 秒處交棒給新連線，下方一條錄音軌從頭到尾不斷；聲音先落進 lecture.wav 再送辨識
- voiceover: "第二層，連線。Gemini 即時辨識的連線有時間上限，課卻有兩小時。所以每五百七十秒，主動換一條新連線，錄音不中斷。聲音也都先寫進音檔，才送去辨識；就算辨識掛掉，錄音還是完整的。"
- duration: 15.95s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/04-rotation.html
- type: feature_showcase
- persuasion: Before/after + Demonstration
- beat: tension → relief
- need: Gemini Live 連線有時間上限，課有兩小時；辨識斷掉時不能連錄音都丟
- design: SESSION_ROTATE_SECONDS 570s 主動輪替（ADR 0003）；wav 先寫入、再進 audio_queue

narrativeRole: 第二個需求→設計對：外部服務的限制被主動輪替與「先落地再送出」吸收。
keyMessage: 連線會換，錄音永遠完整。

- blueprint: compose
- minimap: 亮起 server、live、disk 與 server⇄live、server→disk 連線
- focal: 主舞台的雙軌時間軸（上：Gemini Live 連線段；下：lecture.wav 錄音軌）
- roles: 雙軌時間軸 = foreground subject · 時間刻度 `0 → 120 分鐘` = supporting · 「先寫檔再送辨識」小流程 = supporting · 需求／設計卡 = supporting
- rules: stat-bars-and-fills, svg-path-draw, css-marker-patterns

主舞台上半是雙軌時間軸（橫跨 x 700–1840）：刻度 mono `0`、`30`、`60`、`90`、`120 分鐘`。上軌標籤 `Gemini Live 連線`，下軌標籤 `lecture.wav`。下半（y 680–860）是一條小流程：`麥克風 → lecture.wav → audio_queue → Gemini Live`。
Scene 1 (0.0–2.0s): 頂欄 `LAYER 02 / 07` +「連線」與小地圖就位，小地圖相關方塊亮起；時間刻度淡入。
Scene 2 (2.1–4.8s): 需求卡出現：「連線有時間上限，課卻有兩小時。」上軌出現一段連線條，從 0 往右長，撞到一條豎立的 mono 標記 `上限` 後停住，後面是空的。
Scene 3 (4.9–6.0s): 「課卻有兩小時」：上軌下方一條細的 ink-faint 虛線從 0 延伸到 120 分鐘，標 `一堂課`。
Scene 4 (6.1–8.6s): 設計卡出現：「每 570 秒主動換一條新連線」+ mono `SESSION_ROTATE_SECONDS = 570 · ADR 0003`。上軌被切成 13 段接力條（每段 570 秒，最後一段較短），由左往右 stagger 長出，段與段之間留一條細縫。
Scene 5 (8.7–9.6s): 「錄音不中斷」：下軌 `lecture.wav` 一條連續實心條從 0 一口氣長到 120 分鐘，沒有任何縫。
Scene 6 (9.7–12.5s): 下半小流程依旁白逐站出現：`麥克風` → `lecture.wav`（9.7s，「先寫進音檔」）→ `audio_queue` → `Gemini Live`（11.4s，「才送去辨識」）；設計卡追加一行「聲音先落地，再送辨識」。
Scene 7 (12.6–15.95s): 「就算辨識掛掉」：小流程的 `Gemini Live` 站與上軌中間兩段被 cobalt 斜線刪除；「錄音還是完整的」：下軌 `lecture.wav` 條旁出現 mono `完整`，用 css-marker 方框圈起。之後靜止。

## Frame 5 — 第三層：失敗之後

- scene: 小地圖亮 FastAPI⇄Gemini Flash；主畫面三列方格條（pixel-stack），各代表一種呼叫的等待間隔：2·4·8 秒／5·15·45·120 秒／60·300·900 秒
- voiceover: "第三層，失敗之後。網路一定會壞，但每種失敗的代價不一樣。逐段筆記補不到不致命，兩秒、四秒、八秒就放棄；最終整併最貴，一路等到一百二十秒；還是失敗，背景再隔一分鐘、五分鐘、十五分鐘去試。"
- duration: 16.28s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/05-retries.html
- type: feature_showcase
- persuasion: Comparison of three options + Worked example with real numbers
- beat: "aha"
- blueprint: dataviz-countup
- need: 網路會壞，但三種呼叫失敗的代價不同
- design: LIVE_RETRY_DELAYS (2,4,8) · FINAL_RETRY_DELAYS (5,15,45,120) · BACKGROUND_RETRY_DELAYS (60,300,900)；摘要用串流讀取（ADR 0001）

narrativeRole: 第三個需求→設計對：重試策略依失敗代價分層，不是一套用到底。
keyMessage: 越不能丟的呼叫，等越久。

- blueprint: dataviz-countup (Adapt)
- minimap: 亮起 server、flash 與 server⇄flash 連線
- focal: 主舞台的三列等待間隔方格條（pixel-stack ledger）
- roles: 三列方格條 = foreground subject · 每列的 mono 數字標籤 = supporting · 列名與常數名 = supporting · 需求／設計卡 = supporting
- rules: stat-bars-and-fills, counting-dynamic-scale

Adapt：保留 dataviz-countup「數字是主角、在旁白講到時落地」的骨架；不做相機穿越（層幀版型固定），改成三列 ledger 依序長出。
主舞台是三列 ledger-row，每列左邊是列名（body 600）+ 常數名（mono），右邊是一串方格條：每個等待間隔一根條，條長依 log 尺度（標注 mono `等待間隔 · log 尺度`），條尾寫實際秒數。
- 第 1 列 `逐段筆記` · `LIVE_RETRY_DELAYS`：`2s` `4s` `8s`，列尾標 `放棄 · 逐字稿還在`
- 第 2 列 `最終整併` · `FINAL_RETRY_DELAYS`：`5s` `15s` `45s` `120s`
- 第 3 列 `背景再試` · `BACKGROUND_RETRY_DELAYS`：`1 分` `5 分` `15 分`
Scene 1 (0.0–2.4s): 頂欄 `LAYER 03 / 07` +「失敗之後」與小地圖就位，相關方塊亮起；ledger 的表頭線畫出。
Scene 2 (2.5–5.1s): 需求卡出現：「網路一定會壞，但每種失敗的代價不同。」
Scene 3 (5.2–8.8s): 第 1 列列名出現（5.2s）；三根條在「兩秒、四秒、八秒」(7.0 / 7.6 / 8.0s) 各自長出，數字 count-up 落地；列尾 `放棄 · 逐字稿還在` 在 8.4s 出現。
Scene 4 (8.9–11.4s): 設計卡出現：「依失敗代價，分三層重試」+ mono `ADR 0001 · 串流讀取`。第 2 列列名出現（8.9s），四根條依序長出，最後一根 `120s` 在 10.9s 落地且最長、最醒目（counting-dynamic-scale 讓 120 的字級略大）。
Scene 5 (11.5–16.28s): 第 3 列列名出現（11.5s），三根條在「一分鐘、五分鐘、十五分鐘」(13.1 / 13.4 / 14.0s) 長出。全部到齊後靜止。

## Frame 6 — 第四層：最終筆記

- scene: 小地圖亮 FastAPI 與磁碟；主畫面 finalize_input.json 先落地，兩條路徑（下課收尾／POST /finalize）匯向同一把鎖 session_lock，晚到的「失敗」卡片被擋在鎖外
- voiceover: "第四層，最終筆記。整併之前，素材先存成檔案，失敗了隨時能再跑一次。可是再跑有兩條路：下課收尾，和手動或背景的再整併。兩條路都要排隊拿同一把鎖，晚到的失敗，就蓋不掉已經成功的筆記。"
- duration: 16.48s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/06-two-paths.html
- type: feature_showcase
- persuasion: Signposting + Counterexample（沒有鎖會怎樣）
- beat: unease → conviction
- need: 整併失敗不能讓素材消失；兩條寫入路徑同時跑會互相覆蓋
- design: finalize_input.json 先寫（ADR 0002）；兩條路徑都經 session_lock + publish_final_notes；skip_if_merged 在取得鎖之後檢查

narrativeRole: 第四個需求→設計對：可重跑 + 互斥，保住最貴的那份產出。
keyMessage: 素材先落地、兩條路共用一把鎖。

- blueprint: compose
- minimap: 亮起 server、disk 與 server→disk 連線
- focal: 主舞台的「兩條路徑 → 一把鎖」匯流圖
- roles: 兩條路徑 + session_lock 鎖 + final_notes.md = foreground subject · finalize_input.json 檔案卡 = supporting · 晚到的失敗卡 = supporting（反例）· 需求／設計卡 = supporting
- rules: svg-path-draw, css-marker-patterns, spring-pop-entrance

主舞台左上是檔案卡 `finalize_input.json`（mono，帶一個小磁碟圖示）；中段是兩條由左往右的路徑：上路徑 `① WebSocket 下課收尾`、下路徑 `② POST /finalize（手動・背景）`，兩條匯向中央一個方形鎖圖示 `session_lock`，鎖右邊是 `publish_final_notes` → `final_notes.md · status: ok`。
Scene 1 (0.0–2.4s): 頂欄 `LAYER 04 / 07` +「最終筆記」與小地圖就位，相關方塊亮起。
Scene 2 (2.5–4.7s): 需求卡出現：「整併失敗，素材不能跟著消失。」檔案卡 `finalize_input.json` 落到主舞台左上（從上方 8% 降下，power3）。
Scene 3 (4.8–6.5s): 「失敗了隨時能再跑一次」：檔案卡旁一個循環箭頭 SVG 畫出，標 mono `可重跑 · ADR 0002`。
Scene 4 (6.6–10.7s): 「再跑有兩條路」：上路徑在 7.9s 畫出（「下課收尾」），下路徑在 8.9s 畫出（「手動或背景的再整併」）。
Scene 5 (10.8–12.9s): 設計卡出現：「兩條路都經過同一把鎖」+ mono `session_lock · publish_final_notes`。鎖圖示與右側 `final_notes.md · status: ok` 依序進場，兩條路徑的線在鎖前匯合。
Scene 6 (13.0–16.48s): 「晚到的失敗」：一張標 `失敗 · fallback` 的小卡沿上路徑滑向鎖，停在鎖前，被 cobalt 斜線刪除；「蓋不掉已經成功的筆記」：`status: ok` 用 css-marker 方框圈起。之後靜止。

## Frame 7 — 第五層：人的修正

- scene: 小地圖亮 瀏覽器→FastAPI→Gemini Flash；主畫面一個錯字沿著「段落 → 章節 → 最終」一路被放大；接著「更正」以指示的形式插進三個 prompt，「手寫筆記」原文直接貼到最後
- voiceover: "第五層，人的修正。模型寫錯一段，錯誤會被壓縮、再被整併，一路傳到最後。所以你的更正，被當成指示，注入每一次生成；衝突時，以更正為準。而你的手寫筆記，原文保留，不讓模型轉述。"
- duration: 14.82s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/07-corrections.html
- type: feature_showcase
- persuasion: Causal chain + Comparison of two options（指示 vs 素材）
- beat: concern → mastery
- need: 模型會寫錯，而錯誤會沿著壓縮管線傳染到最終筆記
- design: 更正累積成清單注入 lecture_note_prompt / rollup_prompt / final_prompt；手寫筆記附加在 USER_NOTES_HEADING 底下（ADR 0005）

narrativeRole: 第五個需求→設計對：人的判斷以兩種不同身分進入管線。
keyMessage: 更正是指示，手寫筆記是素材。

- blueprint: compose
- minimap: 亮起 browser、server、flash 與 browser→server、server⇄flash 連線
- focal: 主舞台的三節點傳染鏈（段落筆記 → 章節摘要 → 最終筆記）與兩條修正入口
- roles: 三節點鏈 = foreground subject · 錯誤標記（cobalt 方塊內一個 ×）= supporting · 「更正」卡與三個 prompt 晶片 = foreground（後段）· 「手寫筆記」卡 = supporting · 需求／設計卡 = supporting
- rules: svg-path-draw, css-marker-patterns, scale-swap-transition

主舞台上排是三節點鏈：`段落筆記` → `章節摘要` → `最終筆記`。中排是三個 mono prompt 晶片，各自對齊上排一個節點：`lecture_note_prompt`、`rollup_prompt`、`final_prompt`。下排左邊是「更正」卡，右邊是「手寫筆記」卡。
Scene 1 (0.0–2.1s): 頂欄 `LAYER 05 / 07` +「人的修正」與小地圖就位，相關方塊亮起；上排三節點淡入（還沒有錯誤）。
Scene 2 (2.2–6.0s): 需求卡出現：「模型會寫錯，錯誤還會一路傳下去。」2.2s 第一個節點出現錯誤標記；3.2s（「被壓縮」）錯誤標記跳到第二個節點並變大；4.2s（「再被整併」）跳到第三個節點並再變大；5.0s 三個節點之間的箭頭轉為粗實線。
Scene 3 (6.1–9.3s): 設計卡出現：「更正是指示；手寫筆記是素材。」+ mono `corrections_block() · ADR 0005`。「更正」卡進場（6.1s），三條線從更正卡畫向三個 prompt 晶片（7.9s 起依序），晶片亮起。
Scene 4 (9.4–11.0s): 「衝突時以更正為準」：晶片列上方出現一行 mono 規則 `衝突時以更正為準`，css-marker 底線；上排三個節點的錯誤標記同時被刪除。
Scene 5 (11.1–14.82s): 「手寫筆記原文保留」：「手寫筆記」卡進場，一條線繞過三個 prompt 晶片，直接接到 `最終筆記` 節點下方，接點標 mono `## 我的課堂筆記（原文）`；「不讓模型轉述」時這條線旁加 mono `不經模型`。之後靜止。

## Frame 8 — 第六層：翻譯

- scene: 小地圖亮 Gemini Flash 與磁碟；主畫面 final_notes.md 落地後，才分出 EN / DE / PL / ES 四張譯本；其中一張帶著較舊的時間戳，下載鈕被劃掉
- voiceover: "第六層，翻譯。組員裡，有人不讀中文。翻譯是整併成功之後，獨立的一步，產出英、德、波蘭、西班牙四種語言。翻譯失敗，只少一份譯本；比正本還舊的譯本，一律不能下載。"
- duration: 14.3s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/08-translations.html
- type: feature_showcase
- persuasion: Before/after + Counterexample（舊譯本）
- beat: confidence
- need: 組員有人不讀中文；但翻譯不能拖垮最貴的整併
- design: 整併後獨立一步，失敗時跳過（ADR 0006）；is_current() 比 mtime，舊譯本不進下載

narrativeRole: 第六個需求→設計對：把附加功能隔離在主產出之外。
keyMessage: 譯本永遠不比正本舊。

- blueprint: compose
- minimap: 亮起 flash、disk 與 server⇄flash、server→disk 連線
- focal: 主舞台的正本卡與扇出的四張譯本卡
- roles: 正本卡 `final_notes.md` = foreground subject · 四張譯本卡 = foreground（扇出）· 刪除線與 404 標記 = supporting · 需求／設計卡 = supporting
- rules: center-outward-expansion, css-marker-patterns, grid-card-assemble

主舞台左側是正本卡 `final_notes.md · 繁中正本`；右側四張譯本卡縱向排列：`EN · English`、`DE · German`、`PL · Polish`、`ES · Spanish`，每張卡右下有一個小小的方形下載鈕 `下載`。
Scene 1 (0.0–1.8s): 頂欄 `LAYER 06 / 07` +「翻譯」與小地圖就位，相關方塊亮起。
Scene 2 (1.9–3.7s): 需求卡出現：「組員裡，有人不讀中文。」
Scene 3 (3.8–6.4s): 設計卡出現：「整併成功之後，才獨立翻譯」+ mono `write_translations() · ADR 0006`。正本卡在主舞台左側落定，外框加粗。
Scene 4 (6.5–9.5s): 四張譯本卡從正本卡位置向右扇出（center-outward-expansion），依旁白「英、德、波蘭、西班牙」在 6.8 / 7.4 / 7.8 / 8.2s 各自落定。
Scene 5 (9.6–11.3s): 「翻譯失敗，只少一份譯本」：`PL` 卡被 cobalt 斜線刪除並標 mono `失敗 · 跳過`；正本卡與其他三張不受影響。
Scene 6 (11.4–14.3s): 「比正本還舊的譯本」：`DE` 卡旁出現 mono `mtime < 正本`；「一律不能下載」：它的下載鈕被刪除，旁邊標 mono `404 · is_current()`。之後靜止。

## Frame 9 — 第七層：手機

- scene: 小地圖亮 瀏覽器／手機；主畫面手機 → Tailscale Serve（*.ts.net HTTPS）→ 你的 Mac 127.0.0.1:8000；雲端圖示被劃掉
- voiceover: "最後一層，手機。上課最順手的是手機，但麥克風只在 H T T P S 底下才能開。透過 Tailscale Serve，手機連回自己的 Mac，前端做成 P W A，金鑰和課堂內容，都不必放上雲端。"
- duration: 14.67s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/09-phone.html
- type: feature_showcase
- persuasion: Comparison of options（雲端 vs 自己的 Mac）
- beat: foresight
- need: 用手機錄音；麥克風只在 secure context 能開，server 沒有驗證
- design: Tailscale Serve 提供 *.ts.net HTTPS 轉給 127.0.0.1:8000，前端做成 PWA（ADR 0007）

narrativeRole: 第七個需求→設計對：不放棄「跑在自己機器上」的前提，也能用手機。
keyMessage: 手機連回自己的 Mac，資料不上雲。

- blueprint: compose
- minimap: 亮起 browser 與 browser→server 連線
- focal: 主舞台的「手機 → Tailscale Serve → 你的 Mac」連線圖
- roles: 手機外框 + Mac 方塊 + 隧道線 = foreground subject · HTTPS 鎖章、PWA 晶片 = supporting · 被刪除的雲端外框 = supporting（反例）· 需求／設計卡 = supporting
- rules: svg-path-draw, css-marker-patterns, spring-pop-entrance

主舞台左側是一支手機外框（直立長方形，0 圓角）；右側是 `你的 Mac` 方塊，內有 mono `127.0.0.1:8000`；兩者之間是一條粗的隧道線，標 `Tailscale Serve · *.ts.net HTTPS`。主舞台上方是一朵用直線段畫的方形雲端外框（不用圓弧），裡面兩個 mono 標籤 `API key`、`課堂內容`。
Scene 1 (0.3–2.2s): 頂欄 `LAYER 07 / 07` +「手機」與小地圖就位，相關方塊亮起。
Scene 2 (2.3–3.8s): 需求卡出現：「上課最順手的是手機。」手機外框 svg-path-draw 畫出。
Scene 3 (3.9–6.6s): 手機上方出現方形鎖章 `getUserMedia 只在 HTTPS 開`；需求卡追加一行「麥克風只在 HTTPS 底下才能開」。
Scene 4 (6.7–9.4s): 設計卡出現：「手機透過 Tailscale Serve 連回自己的 Mac」+ mono `ADR 0007`。隧道線從手機畫向右側（6.7s），`你的 Mac` 方塊在 8.0s 進場。
Scene 5 (9.5–10.8s): 「前端做成 PWA」：手機螢幕裡出現一個實心方形晶片 `PWA`。
Scene 6 (10.9–14.67s): 「金鑰和課堂內容」：上方雲端外框與兩個標籤出現（10.9s）；「都不必放上雲端」：雲端外框被 cobalt 斜線刪除（12.4s），設計卡追加 mono `不上雲`。之後靜止。

## Frame 10 — 七個需求

- scene: 鏡頭拉回整張架構圖，七個層的標籤依序貼回各自的方塊上，最後浮出一句收尾
- voiceover: "五個方塊，七個需求。每一個設計，都對得上一個，上課時真的會發生的問題。"
- duration: 7.84s
- transition_in: zoom-through
- status: animated
- src: compositions/frames/10-close.html
- type: branding
- persuasion: Callback（回到第 2 幀的全貌）+ Distillation
- beat: satisfaction + "now I get it"
- blueprint: titlecard-reveal

narrativeRole: 回到全貌，把七個需求釘回各自的方塊，收成一句話。
keyMessage: 架構是需求的地圖。

- blueprint: titlecard-reveal (Adapt)
- minimap: （本幀是全尺寸架構圖，不放小地圖）
- focal: 收尾句（display-closing）
- roles: 全尺寸架構圖 + 七個層標籤 = supporting（收尾句出現後淡到 35%）· 收尾句 = foreground subject · 網格 + 左側 pixel-glitch + QR-block = background
- rules: dynamic-content-sequencing, center-outward-expansion

Adapt：保留 titlecard-reveal「一個克制的動作、然後定格」；在定格前多一拍：七個層標籤釘回架構圖。
Scene 1 (0.0–1.1s): STAGE.md §3 的全尺寸架構圖完整呈現（與 Frame 2 結尾同位置，進場只做淡入，不重組）。
Scene 2 (1.2–1.9s): 「七個需求」：七個 mono 標籤快速依序釘到各自的位置（stagger 總長 ≤ 0.6s）：`01 資料流`（browser→server 連線上）、`02 連線`（server⇄live 連線旁）、`03 失敗之後`（server⇄flash 連線旁）、`04 最終筆記`（disk 下方）、`05 人的修正`（browser 下方）、`06 翻譯`（flash 下方）、`07 手機`（browser 上方）。
Scene 3 (2.0–7.84s): 架構圖與標籤淡到 35%；畫面右側（右錨，≤ 78cqw 寬）出現 display-closing 收尾句，兩行 per-phrase reveal：「每一個設計，」(2.0s) →「都對得上一個」(3.0s) →「上課時真的會發生的問題。」(3.6s)。左側 pixel-glitch 欄與右上 QR-block 在 3.6s 一起出現。之後完全靜止到幀尾；最後 0.6s 整幀淡到 paper（全片唯一的真正結尾）。
