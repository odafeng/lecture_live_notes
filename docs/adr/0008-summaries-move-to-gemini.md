# 8. 筆記、章節與翻譯改走 Gemini 端點

## Status

Accepted（2026-10-20）

## Context

這個 App 一直用兩家服務：Gemini Transcribe Live 做即時語音辨識，Anthropic 的 Claude Haiku 4.5 做逐段筆記、章節摘要、最終整併與翻譯。代價是兩把 key、兩張帳單、兩套錯誤碼與重試規則，README 也得教人申請兩次。使用者要求整個專案改用 Gemini 端點。

改之前先用專案裡那把 key 實際探了一輪（2026-10-20）：

- `GET /v1beta/models` 列出 63 個模型。文字生成可用的 Flash 層級有 `gemini-2.5-flash`、`gemini-3.5-flash`、`gemini-3.5-flash-lite`、`gemini-3.6-flash`、`gemini-3.7-flash`、`gemini-3.8-flash` 等。
- 打 `gemini-3.5-flash` 的 `streamGenerateContent`，回應的 `modelVersion` 是 `gemini-3.6-flash`。這個名字是別名，實際服務的模型會換。
- 預設有 thinking，而且 thought token 計入 `maxOutputTokens`。給 64 token 的預算，60 個被 thinking 用掉，回 `finishReason: MAX_TOKENS`，正文是空字串。一個三句話的提示，thinking 用了 652 token，正文只有 114。
- `thinkingConfig.thinkingLevel: "minimal"` 與 `thinkingBudget: 0` 在 `gemini-3.5-flash` 上都被接受，thought token 歸零。`gemini-3.8-flash` 對 `minimal` 回 400：「Thinking level MINIMAL is not supported for this model」。
- 官方定價頁沒有列 `gemini-3.5-flash`。`gemini-3.6-flash` 與 `gemini-3.8-flash` 同價：每百萬 token 輸入 US$0.75、輸出 US$3.75，到 2026-12-31 為止；2027 年起 US$1.50 與 US$7.50。
- 串流格式：`alt=sse` 時每個 `data:` 行是一份完整的 `GenerateContentResponse`，正文在 `candidates[0].content.parts[].text`，`finishReason` 只掛在最後一個 chunk，而最後一個 chunk 的 part 是空字串加 `thoughtSignature`。整個請求失敗時是帶 HTTP 狀態碼的 `{"error": {"code", "message", "status"}}`。

## Decision

摘要、章節、整併與翻譯全部改打 `POST /v1beta/models/{SUMMARY_MODEL}:streamGenerateContent?alt=sse`，預設模型 `gemini-3.5-flash`。

仍然用 httpx 直打 REST，不引進 `google-genai` SDK。ASR 那條本來就是自己開 websocket，測試靠 `httpx.MockTransport` 攔請求，換 SDK 等於把 fake 也重寫一次。ADR 0001 的串流決定沿用：`read` timeout 管的還是相鄰 chunk 的間隔，不是整段生成。

key 放 `x-goog-api-key` header，不放 query string。錯誤訊息與 log 會帶 URL，query string 裡的 key 會跟著進去。

`generationConfig` 固定為 `maxOutputTokens: 16384` 加 `thinkingConfig.thinkingLevel: "minimal"`。關 thinking 的理由有三個：Haiku 時期本來就沒有 reasoning 步驟，筆記品質不是靠它撐的；thought token 以輸出價計費，上面那個 652 對 114 的比例會讓每分鐘一次的呼叫貴好幾倍；它還會吃掉輸出預算。上限從 8,192 提到 16,384，給 `minimal` 偶爾還是會想一點的情況留空間，最終整併的 5,000 token 正文也遠在限制之下。

完成判定看 `finishReason`。`MAX_TOKENS` 是截斷，報錯不當成完整筆記。`STOP` 才算正常結束。最後一個 chunk 一定帶 finishReason，所以已經收到正文、串流卻在沒有它的情況下乾淨地結束，表示中間被切掉了，視為 `GeminiStreamError` 走重試。其他值（`SAFETY`、`RECITATION`、`OTHER`……）表示模型中途停了，已經收到的半截正文一樣丟掉報錯，而且不重試，因為同樣的輸入再送一次結果通常相同。chunk 裡帶 `error` 的情況視為可重試（`GeminiStreamError`），對應原本 Anthropic 的 `overloaded_error` 事件。帶 `thought: true` 的 part 跳過，對應原本的 `thinking_delta`。

可重試的 HTTP 狀態碼是 429、500、502、503、504。529 拿掉，那是 Anthropic 的碼。

`ANTHROPIC_API_KEY` 整個移除。原本「`.env` 優先於 shell 環境變數」的規則是為它寫的，現在搬到 `GEMINI_API_KEY` 上，理由不變：shell 裡留著一把舊 key 時，專案設定不該被它蓋掉。`/health` 與 `session.json` 的 `summary_provider` 改成 `gemini`，`summary_api_key_configured` 欄位刪除。

## Consequences

一把 key、一張帳單。README 少掉一整節申請流程。

負面與代價：

- thinking 的關法寫死在 `SUMMARY_GENERATION_CONFIG`。把 `SUMMARY_MODEL` 換成不接受 `minimal` 的模型（2026-10-20 的 `gemini-3.8-flash` 就是）會每次都收到 400，400 不重試，每一段筆記都會落成逐字稿 fallback。換模型前得先用 curl 探一次。
- `gemini-3.5-flash` 是會飄的別名。`session.json` 記的是設定裡的名字，不是 `modelVersion`，所以同一個名字在不同日期可能是不同模型，回頭比較筆記品質時要記得這件事。
- 三個 prompt 是對著 Haiku 調的，這次一字未改。筆記的風格、長度與表格使用習慣可能變，要上幾堂課才知道要不要重調。
- 費用估算要重做。ADR 0006 用 Haiku 費率算出的翻譯成本已不適用；README 的 48 分鐘課堂 token 數也是 Haiku tokenizer 算的，換模型後會有出入。
- 舊課堂的 `session.json` 不回填，`summary_provider: "anthropic"` 留在歷史資料裡。
- 上面的探測都只是一次性的 curl，不是測試。模型清單、別名指向、定價與 `minimal` 的支援情況都會變，本文記的是 2026-10-20 當天的觀察。
