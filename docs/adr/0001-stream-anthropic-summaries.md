# 1. 以 streaming 呼叫 Anthropic Messages API

## Status

Accepted（2026-09-08）

## Context

2026-09-08 一堂 48 分鐘的課，37 個逐塊筆記與 3 次章節整併全部成功，只有最後一次整併失敗，`final_notes.md` 落成 fallback：

```
最終整併失敗：RuntimeError: Anthropic 摘要請求失敗（ConnectError），已嘗試 4 次。
```

`server.log` 顯示第一次是 `ReadTimeout`，後三次是 `ConnectError`。當天網路本身有問題：ping api.anthropic.com 的 RTT avg 735ms、max 1598ms、stddev 589ms，第一次 TCP connect 花了 7.17 秒，同一時段多個 MCP server 也 DNS 解析失敗。

事後用同一份素材重跑，input 5437 tokens、output 5064 tokens，耗時 68.3 秒。原本的呼叫是非 streaming，`httpx.AsyncClient(timeout=180)` 的 read timeout 必須一口氣涵蓋整段生成。在回應完成前，一個 byte 都不會到。逐塊筆記輸出短、幾秒就回來，所以撐得過去；最終整併要連續生成 5000 個 token，網路慢個兩三倍就爆掉。

失敗落在最長的那次呼叫上不是巧合。輸出越長，越撐不過那個 180 秒。

## Decision

改用 `stream: true`，以 `client.stream()` 讀 SSE，逐事件累積 `content_block_delta` 的文字。timeout 改成 `httpx.Timeout(connect=15, read=120, write=60, pool=30)`。

`stop_reason` 改從 `message_delta` 事件讀取，維持原本的 `max_tokens` 截斷偵測。SSE 中途出現 `error` 事件（例如 `overloaded_error`）以 `AnthropicStreamError` 表示，納入可重試的例外。

## Consequences

read timeout 從「整段生成的預算」變成「兩個 chunk 之間的預算」，同樣寫 120 秒，容錯遠高於原本的 180 秒。串流中途斷線會拋 `RemoteProtocolError`，屬於 `TransportError`，自動落入既有的重試路徑。

負面與代價：

- SSE 解析是自己寫的，比讀一個 JSON 物件多了會出錯的地方。事件型別（`text_delta` 與 `thinking_delta`）判斷錯就會靜默吐出錯誤內容。
- 沒有整體時間上限。`read` 只管相鄰 chunk 的間隔，理論上一個一直緩慢吐字的回應可以拖很久，實務上由 `max_tokens` 收斂。
- 重試時累積的文字必須重置，否則斷線重試會把前半段接兩次。這裡靠 `stream_anthropic_message()` 每次呼叫都建自己的 buffer 來保證，而不是靠記得清空。測試 `test_dropped_stream_is_retried_without_duplicating_partial_text` 守住這件事。
- 所有測試 fixture 從 JSON 改成 SSE，`tests/fakes.py` 多了 `sse()` 與 `stream_response()`。
