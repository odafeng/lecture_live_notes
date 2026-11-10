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

錄音不受影響。`wav_file.writeframes(data)` 在 `audio_queue.put()` 之前執行，也不看 `asr_failed` 旗標，所以就算 ASR 完全掛掉，`lecture.wav` 仍然完整，事後永遠能從音檔重跑逐字稿。

補記（#11）：原本用的是 `writeframesraw`，它不更新 WAV 檔頭，資料也留在 Python 的寫入緩衝區
（3.14 預設 128 KiB，約 4 秒音訊）直到 `close()`。server 在下課前停掉時，硬碟上的檔案讀不出來，
「錄音仍然完整」只在正常收尾時成立。改用 `writeframes` 後，每包都會改寫檔頭長度，改寫時的
seek 會把 Python 的緩衝區交給作業系統。開檔時先寫一個空 frame，讓檔頭以長度 0 先落地，
否則第一包的長度剛好等於檔頭記的長度、不會觸發改寫，第一包仍會留在記憶體裡。實測一小時約多花
0.4 秒。保證的範圍是 server 程序中途結束；沒有 `fsync`，斷電時作業系統還沒寫出的部分仍可能遺失。

`audio_queue` 的 maxsize 500 約等於 42 秒緩衝，重連 backoff 最多 6 秒，所以重連期間的音訊不會掉。但若斷線超過 42 秒，`audio_queue.put()` 會阻塞接收迴圈，連帶讓 wav 寫入落後。（已由 ADR 0009 取代：queue 滿時丟掉最舊的 chunk，接收迴圈不再阻塞。）

補記（#10）：上面假設 worker 終究會重連、把 queue 取空。若 worker 連續失敗到放棄 ASR 時
queue 剛好是滿的，已經卡在 `put()` 的接收迴圈就再也醒不來：`stop` 讀不到、收尾不會執行。
因此放棄時會先清空 `audio_queue`，接收迴圈的 `put()` 才能返回，之後看到 `asr_failed`
就不再餵 queue。佇列大小與重連間隔也改成模組常數 `AUDIO_QUEUE_CHUNKS`、`ASR_RETRY_DELAYS`。

順帶修掉 `finally` 區塊沒有取回 receiver task 例外的問題。原本只在 task 未完成時才 `await`，已完成且帶例外的 task 就沒人收，asyncio 會把整段 traceback 印進 log。診斷時最難讀的就是這段雜訊。
