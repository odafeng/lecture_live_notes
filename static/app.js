const TARGET_SAMPLE_RATE = 16000;
let audioContext, mediaStream, sourceNode, processorNode, ws;
let recording = false;
let phase = "idle";
let startedAt = 0;
let timerId = null;
let readyResolve = null;
let readyReject = null;
let readyTimeout = null;
let autoScroll = true;
let transcriptCount = 0;
let noteCount = 0;

const $ = id => document.getElementById(id);
const startBtn = $("startBtn"), stopBtn = $("stopBtn"), markBtn = $("markBtn");
const statusEl = $("status"), timerEl = $("timer");
const courseTitleEl = $("courseTitle"), customVocabularyEl = $("customVocabulary"), transcriptionModeEl = $("transcriptionMode");
const transcriptEl = $("transcript"), interimEl = $("interim"), notesEl = $("notes");
const finalPanel = $("finalPanel"), finalEl = $("final"), downloadsEl = $("downloads");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

$("todayDate").textContent = new Intl.DateTimeFormat("zh-TW", {month: "long", day: "numeric", weekday: "long"}).format(new Date());
$("todayDate").dateTime = new Date().toISOString();
if (window.matchMedia("(max-width: 960px)").matches) $("courseSettings").open = false;

function setStatus(text) { statusEl.textContent = text; }
function setPhase(next) {
  phase = next;
  document.body.dataset.phase = next;
  const busy = ["connecting", "recording", "finalizing"].includes(next);
  startBtn.disabled = busy;
  startBtn.hidden = next === "recording";
  stopBtn.hidden = next !== "recording";
  stopBtn.disabled = next !== "recording";
  markBtn.disabled = next !== "recording";
  courseTitleEl.disabled = busy;
  customVocabularyEl.disabled = busy;
  transcriptionModeEl.disabled = busy;
  $("startLabel").textContent = ({connecting: "連線中…", finalizing: "整理筆記中…", complete: "開始新課堂"})[next] || "開始上課";
}
function showNotice(text, source = "general") {
  $("noticeText").textContent = text;
  $("notice").dataset.source = source;
  $("notice").hidden = false;
}
function fmt(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = String(Math.floor(sec / 3600)).padStart(2, "0");
  const m = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}
function startTimer() {
  startedAt = Date.now();
  timerId = setInterval(() => timerEl.textContent = fmt((Date.now() - startedAt) / 1000), 500);
}
function stopTimer() { if (timerId) clearInterval(timerId); timerId = null; }
function downsampleBuffer(buffer, inputRate, outputRate) {
  if (inputRate === outputRate) return buffer;
  if (outputRate > inputRate) throw new Error("Invalid target sample rate");
  const ratio = inputRate / outputRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let out = 0, input = 0;
  while (out < result.length) {
    const next = Math.round((out + 1) * ratio);
    let sum = 0, count = 0;
    for (let i = input; i < next && i < buffer.length; i++) { sum += buffer[i]; count++; }
    result[out++] = count ? sum / count : 0;
    input = next;
  }
  return result;
}
function floatToPCM16(arr) {
  const buf = new ArrayBuffer(arr.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < arr.length; i++) {
    const s = Math.max(-1, Math.min(1, arr[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}
function updateAudioLevel(input) {
  let energy = 0;
  for (let i = 0; i < input.length; i++) energy += input[i] * input[i];
  const level = Math.min(100, Math.round(Math.sqrt(energy / input.length) * 250));
  $("audioMeter").setAttribute("aria-valuenow", level);
  $("audioMeter").firstElementChild.style.transform = `scaleX(${level / 100})`;
}
function websocketUrl() {
  return `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;
}
function scrollToLatest(el) {
  if (autoScroll) {
    const container = el.closest(".panel-body");
    container.scrollTop = container.scrollHeight;
  }
}
function appendAndScroll(el, text) {
  const entry = document.createElement("div");
  entry.className = "text-entry";
  entry.textContent = text;
  el.appendChild(entry);
  if (el === notesEl) $("notesEmpty").hidden = true;
  scrollToLatest(el);
}
function appendTranscript(line) {
  const entry = document.createElement("div");
  entry.className = "transcript-entry";
  const match = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/s);
  if (match) {
    const stamp = document.createElement("time");
    stamp.textContent = match[1];
    entry.appendChild(stamp);
  }
  const text = document.createElement("p");
  text.textContent = match ? match[2] : line;
  entry.appendChild(text);
  transcriptEl.appendChild(entry);
  $("transcriptEmpty").hidden = true;
  $("transcriptCount").textContent = `${++transcriptCount} 段`;
  scrollToLatest(transcriptEl);
}
function appendMarkdown(html) {
  const entry = document.createElement("div");
  entry.className = "markdown";
  entry.innerHTML = html;
  notesEl.appendChild(entry);
  $("notesEmpty").hidden = true;
  $("noteCount").textContent = `${++noteCount} 段筆記`;
  scrollToLatest(notesEl);
}
function parseVocabulary() {
  return customVocabularyEl.value.split(/[,，\n]/).map(x => x.trim()).filter(Boolean);
}
function settleReady(error) {
  clearTimeout(readyTimeout);
  readyTimeout = null;
  const resolve = readyResolve, reject = readyReject;
  readyResolve = null; readyReject = null;
  if (error) { if (reject) reject(error); }
  else if (resolve) resolve();
}
function renderDownloads(files) {
  downloadsEl.replaceChildren();
  const labels = {final_notes: ["完整筆記", "MD"], live_notes: ["即時筆記", "MD"], transcript: ["逐字稿", "TXT"], audio: ["錄音 WAV", "WAV"], metadata: ["課堂資訊", "JSON"]};
  for (const [key, [label, type]] of Object.entries(labels)) {
    if (!files[key]) continue;
    const a = document.createElement("a");
    a.href = files[key]; a.download = ""; a.setAttribute("aria-label", label);
    a.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-file"/></svg>';
    const name = document.createElement("span");
    name.textContent = label;
    const format = document.createElement("span");
    format.className = "file-type"; format.textContent = type; format.setAttribute("aria-hidden", "true");
    a.append(name, format);
    downloadsEl.appendChild(a);
  }
}
function handleMessage(event) {
  const msg = JSON.parse(event.data);
  if (msg.type === "ready") settleReady();
  if (msg.type === "asr_session") {
    if (recording) setStatus("正在聆聽，記錄每一句話");
    if ($("notice").dataset.source === "asr") $("notice").hidden = true;
  }
  if (msg.type === "status" && recording) setStatus("轉錄重新連線中，錄音持續保存");
  if (msg.type === "asr_error") {
    showNotice("語音辨識暫時中斷，正在重新連線。錄音仍會保存。", "asr");
    appendAndScroll(notesEl, `[語音辨識] ${msg.text}`);
  }
  if (msg.type === "transcript") appendTranscript(msg.line);
  if (msg.type === "interim") {
    interimEl.textContent = msg.text ? `… ${msg.text}` : "";
    interimEl.hidden = !msg.text;
    if (msg.text) { $("transcriptEmpty").hidden = true; scrollToLatest(transcriptEl); }
  }
  if (msg.type === "note_block") appendMarkdown(msg.html);
  if (msg.type === "marker") appendAndScroll(notesEl, msg.text.replace(/^>\s*/, ""));
  if (msg.type === "chapter") appendAndScroll(notesEl, "✓ 已完成背景章節整併");
  if (msg.type === "summary_error") { showNotice(msg.text); appendAndScroll(notesEl, msg.text); }
  if (msg.type === "final") {
    finalPanel.hidden = false;
    finalEl.innerHTML = msg.html;
    finalPanel.scrollIntoView({behavior: reducedMotion.matches ? "instant" : "smooth", block: "start"});
  }
  if (msg.type === "saved") {
    renderDownloads(msg.files || {});
    $("savedBadge").hidden = false;
    setPhase("complete");
    setStatus("本堂課已儲存，筆記準備好複習了");
  }
  if (msg.type === "error") {
    showNotice(msg.text);
    settleReady(new Error(msg.text));
  }
}
function resetWorkspace() {
  transcriptEl.replaceChildren(); notesEl.replaceChildren(); finalEl.replaceChildren(); downloadsEl.replaceChildren();
  interimEl.textContent = ""; interimEl.hidden = true;
  $("transcriptEmpty").hidden = false; $("notesEmpty").hidden = false;
  finalPanel.hidden = true; $("savedBadge").hidden = true; $("notice").hidden = true;
  transcriptCount = 0; noteCount = 0;
  $("transcriptCount").textContent = "0 段"; $("noteCount").textContent = "0 段筆記";
  timerEl.textContent = "00:00:00";
  $("workspaceTitle").textContent = courseTitleEl.value.trim() || "今天，專心上課。";
}
async function releaseAudio() {
  recording = false;
  stopTimer();
  if (processorNode) { processorNode.onaudioprocess = null; processorNode.disconnect(); processorNode = null; }
  if (sourceNode) { sourceNode.disconnect(); sourceNode = null; }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
  const context = audioContext;
  audioContext = null;
  $("audioLabel").textContent = "麥克風已關閉";
  $("audioMeter").setAttribute("aria-valuenow", "0");
  $("audioMeter").firstElementChild.style.transform = "scaleX(0)";
  if (context) await context.close();
}
async function startRecording() {
  if (["connecting", "recording", "finalizing"].includes(phase)) return;
  setPhase("connecting");
  resetWorkspace();
  setStatus("正在連接語音轉錄服務…");
  if (ws) ws.close();
  const socket = new WebSocket(websocketUrl());
  ws = socket;
  socket.binaryType = "arraybuffer";
  socket.onmessage = event => { if (ws === socket) handleMessage(event); };
  socket.onclose = () => {
    if (ws !== socket || ["complete", "error"].includes(phase)) return;
    settleReady(new Error("與伺服器的連線已中斷"));
    setPhase("error");
    setStatus("連線已中斷");
    showNotice("連線已中斷。已寫入的課堂檔案仍保存在本機，請確認 server 是否正在執行。");
    releaseAudio().catch(error => showNotice(error.message));
  };
  const readyPromise = new Promise((resolve, reject) => {
    readyResolve = resolve; readyReject = reject;
    readyTimeout = setTimeout(() => settleReady(new Error("連線等候逾時，請確認 server 與 API 設定")), 15000);
    socket.onerror = () => settleReady(new Error("無法連上伺服器，請確認 server 已啟動"));
    socket.onopen = () => socket.send(JSON.stringify({
      type: "meta", course_title: courseTitleEl.value.trim(),
      custom_vocabulary: parseVocabulary(), transcription_mode: transcriptionModeEl.value,
    }));
  });
  await readyPromise;
  setStatus("請允許使用麥克風…");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true},
  });
  if (ws !== socket || socket.readyState !== WebSocket.OPEN) {
    stream.getTracks().forEach(t => t.stop());
    return;
  }
  const context = new AudioContext();
  await context.resume();
  if (ws !== socket || socket.readyState !== WebSocket.OPEN) {
    stream.getTracks().forEach(t => t.stop());
    await context.close();
    return;
  }
  mediaStream = stream;
  audioContext = context;
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  // 4096 input frames at 48 kHz become about 85 ms of audio at 16 kHz.
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);
  processorNode.onaudioprocess = event => {
    if (!recording || socket.readyState !== WebSocket.OPEN) return;
    const input = event.inputBuffer.getChannelData(0);
    updateAudioLevel(input);
    socket.send(floatToPCM16(downsampleBuffer(input, audioContext.sampleRate, TARGET_SAMPLE_RATE)));
  };
  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);
  recording = true;
  setPhase("recording");
  setStatus("正在聆聽，記錄每一句話");
  $("audioLabel").textContent = "麥克風收音中";
  if (window.matchMedia("(max-width: 960px)").matches) $("courseSettings").open = false;
  startTimer();
}
async function stopRecording() {
  if (!recording) return;
  const socket = ws;
  setPhase("finalizing");
  setStatus("錄音已結束，正在整理完整筆記…");
  interimEl.textContent = ""; interimEl.hidden = true;
  await releaseAudio();
  if (ws === socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: "stop"}));
}
startBtn.addEventListener("click", () => startRecording().catch(async error => {
  const failedSocket = ws;
  settleReady(error);
  setPhase("error");
  setStatus("暫時無法開始上課");
  const text = error.name === "NotAllowedError" ? "麥克風權限未開啟。請在瀏覽器的網站設定中允許麥克風，再試一次。" : error.message;
  showNotice(text);
  await releaseAudio();
  if (ws === failedSocket && failedSocket) failedSocket.close();
}));
stopBtn.addEventListener("click", () => stopRecording().catch(error => { showNotice(`停止錄音時發生問題：${error.message}`); }));
markBtn.addEventListener("click", () => {
  if (recording && ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type: "mark"}));
});
$("followBtn").addEventListener("click", () => {
  autoScroll = !autoScroll;
  $("followBtn").setAttribute("aria-pressed", String(autoScroll));
  if (autoScroll) { scrollToLatest(transcriptEl); scrollToLatest(notesEl); }
});
function setMobileView(view) {
  $("workArea").dataset.view = view;
  $("transcriptViewBtn").setAttribute("aria-pressed", String(view === "transcript"));
  $("notesViewBtn").setAttribute("aria-pressed", String(view === "notes"));
  scrollToLatest(view === "notes" ? notesEl : transcriptEl);
}
$("transcriptViewBtn").addEventListener("click", () => setMobileView("transcript"));
$("notesViewBtn").addEventListener("click", () => setMobileView("notes"));
transcriptionModeEl.addEventListener("change", () => {
  $("modeHelp").textContent = transcriptionModeEl.value === "VERBATIM" ? "盡量保留原始措辭與口頭語句。" : "省略口頭贅詞，讓內容更好讀。";
});
courseTitleEl.addEventListener("input", () => { $("workspaceTitle").textContent = courseTitleEl.value.trim() || "今天，專心上課。"; });
