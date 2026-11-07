# 製作狀態

（過渡用的狀態檔：這部影片的工作還沒開 GitHub issue，跨 session 的進度先記在這裡。）

## 2026-11-07

- v1（cobalt-grid、十張投影片＋push-slide 轉場）已 render 並交付，commit `3063fce`。
  使用者看完否決：風格太醜、字體方正、要「精緻、會 zoom in 區塊、視線跟著線條走」的動畫。
- 決定（使用者選的）：手繪白板風；中文字用思源黑體細字重（Noto Sans TC 300/400/500）。
- v2 架構：整支片改成一張大白板 `compositions/board.html` 加一台虛擬鏡頭。
  鏡頭位置只由時間決定（`camAt(t)`），可以任意 seek；`follow()` 讓線條畫到哪、鏡頭跟到哪。
  旁白、BGM、時間點全部沿用 v1。`index.html` 只掛 board 一個 clip，十個舊 frame 檔已不再引用（還沒刪）。
- 使用者看完 35 秒樣片：方向可以，但鏡頭太快。
- v2 完成並交付（`renders/video-v2.mp4`，Taildrop 到 S24）：
  - 全片 135.97 秒，七層加收尾都在同一張白板上；響度 −13.8 LUFS，true peak −1.6 dBTP。
  - 鏡頭放慢：移動改用 sine.inOut、拉長時間、長途移動中段先拉遠。用 Playwright 逐格量畫面最大位移速度：
    最高約 1,100 px/s（第六層飛往第七層那段），多數移動在 700–900 px/s；樣片裡每秒 9,000 px 以上的瞬間跳點已修掉。
  - 修掉一個 bug：GSAP 預設把 px 值取整，`pathLength=1` 的筆畫因此只會 0 或 1，線條是整條瞬間出現。
    35 秒樣片也有這個問題。現在 `draw()` 帶 `autoRound: false`。
- 還沒做：`compositions/frames/` 底下十個 v1 frame 檔已經沒有被引用，要刪的話等使用者確認。
- 下一步：等使用者看 v2 的意見。
