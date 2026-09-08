# 3. 放寬 Gemini Live 的 keepalive 期限

## Status

Accepted（2026-09-08）

## Context

同一天稍晚，語音辨識反覆中斷。`server.log` 出現 6 次：

```
websockets.exceptions.ConnectionClosedError: sent 1011 (internal error) keepalive ping timeout
```

`sent 1011` 表示是我們這端主動判定連線已死並關閉，不是 Google 把連線踢掉。原本設定 `ping_interval=20, ping_timeout=20`，送出 ping 後 20 秒內沒收到 pong 就重連。

問題在於 keepalive ping 跟音訊 frame 排在同一個 send queue。瀏覽器每 85ms 送一個 chunk，base64 編碼後約 4KB，穩定佔用約 47KB/s 上行。上行一慢，音訊就積壓，ping frame 被推到佇列尾端。pong 遲到的原因是排隊延遲，不只是 RTT。

而且會自我惡化：上行越塞，ping 越晚送出，越容易 timeout；斷線重連期間音訊繼續在 queue 累積，重連後一次灌出去，更塞。

## Decision

`ping_interval` 20 → 30 秒，`ping_timeout` 20 → 60 秒，兩者都改由環境變數 `GEMINI_PING_INTERVAL_SECONDS` 與 `GEMINI_PING_TIMEOUT_SECONDS` 控制。

## Consequences

真正斷掉的連線要多花大約 40 秒才會被偵測到並重連，這段時間的逐字稿會掉。這是刻意的取捨：誤判健康連線的代價（每次斷線都掉一段逐字稿，而且在爛網路下反覆發生）高於延後偵測真正斷線的代價。

錄音不受影響。`wav_file.writeframesraw(data)` 在 `audio_queue.put()` 之前執行，也不看 `asr_failed` 旗標，所以就算 ASR 完全掛掉，`lecture.wav` 仍然完整，事後永遠能從音檔重跑逐字稿。

`audio_queue` 的 maxsize 500 約等於 42 秒緩衝，重連 backoff 最多 6 秒，所以重連期間的音訊不會掉。但若斷線超過 42 秒，`audio_queue.put()` 會阻塞接收迴圈，連帶讓 wav 寫入落後。

順帶修掉 `finally` 區塊沒有取回 receiver task 例外的問題。原本只在 task 未完成時才 `await`，已完成且帶例外的 task 就沒人收，asyncio 會把整段 traceback 印進 log。診斷時最難讀的就是這段雜訊。
