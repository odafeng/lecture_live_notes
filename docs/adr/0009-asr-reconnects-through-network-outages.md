# 9. 斷網期間 ASR 持續重連，只有 Gemini 拒絕才放棄

## Status

Accepted（2026-11-10）

## Context

跑 server 的 Mac 在上課中途斷網，網路恢復後逐字稿沒有接回來。錄音照常保存，但這堂課剩下的時間都沒有即時逐字稿，也就沒有即時筆記。

原本的規則是 Gemini Live 連續失敗 5 次（`len(ASR_RETRY_DELAYS) + 1`）就設定 `asr_failed`，並結束 ASR worker。這個規則沒有區分失敗的原因。斷網時 DNS 解析立刻失敗，每次 connect 幾毫秒就回來，5 次嘗試加上 1.5、3、4.5、6 秒的間隔，總共只撐大約 15 秒。斷線如果是無聲的，還要先等 keepalive 逾時（最長約 90 秒，ADR 0003）才會開始重連。所以只要偵測到斷線後網路又斷超過 15 秒，這堂課的轉錄就結束了。

放棄這條規則本身仍然有用。API key 錯誤或 model 名稱錯誤時，每次重連都會被同樣拒絕，無限重試只會在筆記欄洗版。

另一個限制來自 `audio_queue`。它只能放 500 個 chunk，約 42 秒。原本接收迴圈用 `await audio_queue.put()`，queue 滿了就會卡住，`lecture.wav` 也跟著停止寫入，`stop` 訊息也讀不到（#10）。只要 worker 會重試超過 42 秒，這個問題一定會發生。

## Decision

1. 失敗分成兩類，由 `is_network_failure()` 判斷：
   - 網路失敗：`OSError`（含 DNS 的 `gaierror`、`ConnectionRefusedError`）、`TimeoutError`，以及沒有收到對方 close frame 的 `ConnectionClosed`（例如 keepalive 逾時，是我們這端送出 1011）。這類失敗在下課前一直重試，間隔沿用 `ASR_RETRY_DELAYS`，到最後一個值（6 秒）後就固定不變。
   - Gemini 拒絕：其他所有錯誤。預期的情況是 Gemini 送 close frame 附上原因（例如 1008 policy violation）。不過「錯的 key 會收到哪個 close code」這件事沒有實測過。這類維持原規則，連續 5 次就放棄。
2. 每次斷網只送一次 `asr_error`，避免每 6 秒在筆記欄多一行。重連成功後前端收到 `asr_session`，提示就會收起來。
3. 音訊一律用 `enqueue_audio()` 放進 queue，這個函式從不等待：queue 滿了就丟掉最舊的 chunk。斷網超過約 42 秒時，網路恢復後只會補轉最後約 42 秒，更早的部分只存在 `lecture.wav` 裡，不會有即時逐字稿。
4. 下課（`class_over`）會中斷重連前的等待，並且只再試一次。成功的話送出緩衝的音訊再結束；失敗就直接結束，不讓收尾流程等滿 15 秒。

## Consequences

- 斷網多久都不會讓這堂課永久失去轉錄，網路恢復後最多約 6 秒內會重連。
- 斷網超過約 42 秒的那段時間不會有即時逐字稿和即時筆記，只能事後從 `lecture.wav` 重跑。這是刻意的取捨：全部補轉需要把 queue 改成存在硬碟上，而且恢復後即時字幕會落後好幾分鐘。
- 分類是看例外的型別，不是看錯誤的實際語意。如果 Gemini 未來改成不送 close frame、直接斷 TCP 來拒絕連線，一把錯誤的 key 會被當成網路失敗，每 6 秒重試一次直到下課。代價是不停重試加上一則提示，錄音不受影響。
- 下課時如果剛好碰上一次網路失敗，只會再試一次，不會像以前那樣最多試 5 次。這次失敗之前還在緩衝裡的最後幾秒可能不會進逐字稿。
- 補轉的那段逐字稿，時間戳記的是它從 Gemini 回來的時間，不是實際說話的時間，最多可能差約 42 秒。這是原本 session 輪替時就有的行為，這次沒有處理。
