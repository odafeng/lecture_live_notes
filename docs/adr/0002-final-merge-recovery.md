# 2. 最終整併失敗後的復原路徑

## Status

Accepted（2026-09-08）

## Context

最終整併失敗時，原本的行為是把章節摘要與未整併筆記原樣寫進 `final_notes.md`，附上錯誤訊息。素材沒丟，但沒有任何重跑的路徑。要救回來只能手動撈檔案、自己拼 prompt、自己呼叫 API。2026-09-08 那次就是這樣救的。

這裡其實有兩種不同的失效情境，各自需要不同的東西：

- server 還活著，只是當下網路不通。此時可以在背景繼續重試。
- 使用者下課後直接關機。server 進程一起死，背景重試救不到，只能等下次開 app。

只做其中一個就會留下漏洞。

## Decision

整併前先把素材寫成 `finalize_input.json`（`course_title`、`chapters`、`remaining`），不管整併成功與否都寫。整併結果記在 `session.json` 的 `final_notes_status` 欄位（`ok` / `failed`）。

在這之上提供三條路徑：

- `POST /finalize/{session_id}` 重跑整併，覆寫 `final_notes.md` 並把狀態改回 `ok`。
- 整併失敗時 spawn 背景 task，依 `BACKGROUND_RETRY_DELAYS`（60、300、900 秒）重試，成功即停。
- `GET /sessions/incomplete` 列出狀態為 `failed` 且素材還在的課。前端載入時查詢，顯示「重新整併」按鈕。

前端在完整筆記面板也常駐一顆「重新整併」按鈕，成功的課也能按。重跑一次的成本很低，而筆記品質不滿意想重生成是合理需求。

## Consequences

重跑讀的是 `finalize_input.json`，不是去 parse 自己產生的 `final_notes.md`。這一點刻意為之：parse 自己輸出的 markdown 是脆弱的耦合，使用者手動編輯過筆記就會爛掉。代價是每堂課多一個檔案。

背景重試的節奏（60 / 300 / 900 秒，總共約 21 分鐘後放棄）是猜的，沒有數據支撐。放棄後狀態仍是 `failed`，下次開 app 會提示，所以猜錯的後果有限。

其他負面：

- `spawn_background_finalize()` 建立的 task 必須存進 module-level 的 `_background_tasks`，否則會被 GC 回收。這是 asyncio 的已知陷阱，測試 `test_spawned_task_is_referenced_so_it_is_not_garbage_collected` 守住它。
- 背景重試在 websocket 關閉後才跑，前端收不到結果通知，只有檔案會更新。
- `POST /finalize/{session_id}` 沒有任何認證。server 綁在 127.0.0.1，目前可接受。
