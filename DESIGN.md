# Lecture UI

## Direction

暖白紙感的課堂工作台。以閱讀為中心，像安靜的書桌與筆記紙張；使用真實的錄音、逐字稿和筆記狀態，不展示虛構課程或統計。

## Color

- Canvas: `#f7f7f2`。
- Sidebar: `#eeefe7`。
- Paper: `#fffefa`。
- Ink: `#283d33`。
- Primary action: `#315745`；hover: `#244735`。
- Divider: `#dce0d4`。
- Error: `#a04337`，搭配 `#fcf0ec`。
- 墨綠用於主要操作與閱讀層級；磚紅用於停止與錯誤。用文字同時說明狀態。

## Typography

- 閱讀與操作：Avenir Next / PingFang TC / Microsoft JhengHei。
- 工作台、空白狀態與文件標題：Iowan Old Style / Songti TC / Noto Serif TC。
- 計時器與時間戳：等寬字體與 tabular numerals。
- 所有字體使用本機 fallback，不需外部字型服務。

## Layout

- Desktop：264px 課程設定側欄，主區為錄音控制與並排閱讀區。
- 960px 以下：設定預設折疊，逐字稿與課堂筆記透過按鈕切換。
- 完整筆記採文件閱讀版面，下載區依完整筆記、完整筆記 HTML、整包 ZIP、即時筆記、逐字稿、錄音、課堂資訊排列。
- 課堂輸入框固定在筆記面板底部，介於閱讀區與 footer 之間；560px 以下模式切換換行佔滿整列。

## Components and states

- 使用 5–10px 圓角、細分隔線、低對比紙張背景。
- 錄音流程：idle → connecting → recording → finalizing → complete；失敗進入 error。
- connecting 與 finalizing 期間禁止開始另一堂課；完成儲存後才解鎖。
- 收音指示根據真實 audio sample 的 RMS 更新。
- 課堂輸入框只在 recording 啟用，與「標記重點」同一組規則；空白內容不送出，輸入框保留原值。
- 筆記／更正以 aria-pressed 的雙鍵切換，沿用行動版閱讀切換的樣式語彙；送出鍵的標籤以 visually-hidden 提供。
- 手寫筆記與更正以 textContent 呈現，不走 Markdown renderer，且不計入「段筆記」計數。
- 訊息與逐字稿使用 textContent；筆記使用後端停用 raw HTML 的 Markdown renderer。
- 鍵盤 focus 清楚可見；狀態以 aria-live 宣告，錯誤以 role=alert 顯示。
- 支援 prefers-reduced-motion。自動捲動可關閉，讓使用者閱讀先前的內容。

## Notes document (final_notes.html)

- 獨立檔案：樣式內嵌，不連外部字型或 CDN，不含 script，離線與列印都成立。
- 沿用工作台的紙感色票與字體堆疊，但放大為閱讀尺寸（16px / 1.75）。
- 表格可橫向捲動；列印時去掉背景與邊界留白。
