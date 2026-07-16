const DEVICE_ID_KEY = "fund_estimator_device_id";
const HISTORY_PREFIX = "fund_ai_chat_history_v1";
const AUTO_SPEAK_KEY = "fund_ai_chat_auto_speak_v1";
const MAX_SAVED_MESSAGES = 100;
const MAX_STREAM_HISTORY = 16;
const MAX_RECORDING_MS = 60_000;

function createDeviceId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
  return `device-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function getDeviceId() {
  try {
    let value = window.localStorage.getItem(DEVICE_ID_KEY);
    if (!value) {
      value = createDeviceId();
      window.localStorage.setItem(DEVICE_ID_KEY, value);
    }
    return value;
  } catch {
    return "default";
  }
}

const state = {
  deviceId: getDeviceId(),
  messages: [],
  sending: false,
  abortController: null,
  recorder: null,
  recordingStream: null,
  recordingChunks: [],
  recordingTimer: null,
  recordingMimeType: "",
  speechBuffer: "",
  autoSpeak: loadAutoSpeak(),
};

const els = {
  statusText: document.querySelector("#statusText"),
  loginPanel: document.querySelector("#loginPanel"),
  loginForm: document.querySelector("#loginForm"),
  passwordInput: document.querySelector("#passwordInput"),
  loginBtn: document.querySelector("#loginBtn"),
  loginError: document.querySelector("#loginError"),
  chatPanel: document.querySelector("#chatPanel"),
  chatMessages: document.querySelector("#chatMessages"),
  chatError: document.querySelector("#chatError"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#messageInput"),
  sendBtn: document.querySelector("#sendBtn"),
  stopBtn: document.querySelector("#stopBtn"),
  recordBtn: document.querySelector("#recordBtn"),
  replayBtn: document.querySelector("#replayBtn"),
  autoSpeakToggle: document.querySelector("#autoSpeakToggle"),
  stopSpeakBtn: document.querySelector("#stopSpeakBtn"),
};

function loadAutoSpeak() {
  try {
    return window.localStorage.getItem(AUTO_SPEAK_KEY) !== "0";
  } catch {
    return true;
  }
}

function saveAutoSpeak(value) {
  try {
    window.localStorage.setItem(AUTO_SPEAK_KEY, value ? "1" : "0");
  } catch {
    // The setting is optional when local storage is unavailable.
  }
}

function historyKey() {
  return `${HISTORY_PREFIX}:${state.deviceId}`;
}

function loadHistory() {
  try {
    const raw = window.localStorage.getItem(historyKey());
    const parsed = JSON.parse(raw || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && ["user", "assistant"].includes(item.role) && typeof item.content === "string" && item.content.trim())
      .slice(-MAX_SAVED_MESSAGES)
      .map((item) => ({ role: item.role, content: item.content.trim() }));
  } catch {
    return [];
  }
}

function saveHistory() {
  try {
    window.localStorage.setItem(historyKey(), JSON.stringify(state.messages.slice(-MAX_SAVED_MESSAGES)));
  } catch {
    // The chat stays usable even if local storage is full or disabled.
  }
}

function setError(message = "") {
  els.chatError.hidden = !message;
  els.chatError.textContent = message;
}

function setLoginError(message = "") {
  els.loginError.hidden = !message;
  els.loginError.textContent = message;
}

function setStatus(message) {
  els.statusText.textContent = message;
}

function scrollMessagesToBottom() {
  requestAnimationFrame(() => {
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
  });
}

function messageNode(message) {
  const row = document.createElement("article");
  row.className = `message-row ${message.role}`;
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  if (message.greeting) bubble.classList.add("greeting");
  if (message.streaming) bubble.classList.add("streaming");
  bubble.textContent = message.content;
  row.appendChild(bubble);
  return row;
}

function renderMessages() {
  const fragment = document.createDocumentFragment();
  const messages = state.messages.length
    ? state.messages
    : [{ role: "assistant", content: "你好，我会结合本设备的自选基金与最新可用估值信息回答你的问题。", greeting: true }];
  for (const message of messages) fragment.appendChild(messageNode(message));
  els.chatMessages.replaceChildren(fragment);
  scrollMessagesToBottom();
}

function renderControls() {
  const recording = Boolean(state.recorder && state.recorder.state === "recording");
  els.sendBtn.disabled = state.sending || recording;
  els.stopBtn.hidden = !state.sending;
  els.recordBtn.disabled = state.sending;
  els.recordBtn.classList.toggle("recording", recording);
  els.recordBtn.title = recording ? "点击结束录音并直接发送" : "点击开始录音，再次点击结束后直接发送";
  els.recordBtn.setAttribute("aria-label", recording ? "结束录音" : "语音提问");
}

function resizeInput() {
  els.messageInput.style.height = "auto";
  els.messageInput.style.height = `${Math.min(els.messageInput.scrollHeight, 132)}px`;
}

async function api(path, options = {}) {
  const headers = {
    "X-Device-Id": state.deviceId,
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error?.message || "请求失败，请稍后重试");
  return payload;
}

function showChat() {
  els.loginPanel.hidden = true;
  els.chatPanel.hidden = false;
  state.messages = loadHistory();
  renderMessages();
  els.messageInput.focus();
}

async function loadStatus() {
  try {
    const status = await api("/api/ai-chat/status");
    if (!status.enabled) {
      setStatus("AI 咨询暂未启用");
      els.loginPanel.hidden = false;
      els.loginForm.hidden = true;
      return;
    }
    if (!status.authenticated) {
      setStatus("请输入咨询密码");
      els.loginPanel.hidden = false;
      els.loginForm.hidden = false;
      els.passwordInput.focus();
      return;
    }
    if (!status.model_configured) {
      setStatus("模型服务尚未配置");
      els.loginPanel.hidden = false;
      els.loginForm.hidden = true;
      return;
    }
    setStatus(status.voice_input_available ? "已关联本设备自选基金，可语音或文字咨询" : "已关联本设备自选基金");
    showChat();
  } catch (error) {
    setStatus(error.message);
    els.loginPanel.hidden = false;
    els.loginForm.hidden = true;
  }
}

async function login(event) {
  event.preventDefault();
  const password = els.passwordInput.value.trim();
  if (!password) return;
  els.loginBtn.disabled = true;
  setLoginError("");
  try {
    const status = await api("/api/ai-chat/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    els.passwordInput.value = "";
    if (!status.model_configured) {
      setStatus("模型服务尚未配置");
      els.loginForm.hidden = true;
      return;
    }
    setStatus("验证成功，正在进入咨询");
    window.location.reload();
  } catch (error) {
    setLoginError(error.message);
  } finally {
    els.loginBtn.disabled = false;
  }
}

function parseSseBlock(block) {
  let event = "message";
  const lines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) lines.push(line.slice(5).trim());
  }
  if (!lines.length) return null;
  try {
    return { event, data: JSON.parse(lines.join("\n")) };
  } catch {
    return null;
  }
}

async function sendMessage(event, directMessage = null) {
  event?.preventDefault();
  const message = String(directMessage ?? els.messageInput.value).trim();
  if (!message || state.sending) return;
  stopSpeaking();
  setError("");
  const history = state.messages.slice(-MAX_STREAM_HISTORY);
  state.messages.push({ role: "user", content: message });
  const answer = { role: "assistant", content: "", streaming: true };
  state.messages.push(answer);
  els.messageInput.value = "";
  resizeInput();
  state.sending = true;
  state.abortController = new AbortController();
  state.speechBuffer = "";
  renderMessages();
  renderControls();
  try {
    const response = await fetch("/api/ai-chat/stream", {
      method: "POST",
      credentials: "same-origin",
      signal: state.abortController.signal,
      headers: { "Content-Type": "application/json", "X-Device-Id": state.deviceId },
      body: JSON.stringify({ message, history }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(data?.error?.message || "AI 回复请求失败");
    }
    if (!response.body) throw new Error("浏览器不支持流式回复");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done = false;
    while (!done) {
      const chunk = await reader.read();
      done = chunk.done;
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const eventData = parseSseBlock(block);
        if (!eventData) continue;
        if (eventData.event === "delta") {
          const delta = String(eventData.data?.text || "");
          if (!delta) continue;
          answer.content += delta;
          queueSpeech(delta);
          renderMessages();
        } else if (eventData.event === "error") {
          throw new Error(eventData.data?.message || "AI 回复失败");
        }
      }
    }
    flushSpeech();
    if (!answer.content.trim()) throw new Error("AI 没有返回有效回答");
  } catch (error) {
    if (error.name !== "AbortError") {
      setError(error.message || "AI 回复失败");
    } else if (!answer.content.trim()) {
      state.messages.pop();
    }
  } finally {
    answer.streaming = false;
    state.sending = false;
    state.abortController = null;
    saveHistory();
    renderMessages();
    renderControls();
  }
}

function stopGeneration() {
  state.abortController?.abort();
}

function canSpeak() {
  return state.autoSpeak && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

function speak(text) {
  if (!canSpeak() || !text.trim()) return;
  const utterance = new SpeechSynthesisUtterance(text.trim());
  utterance.lang = "zh-CN";
  utterance.rate = 1.03;
  window.speechSynthesis.speak(utterance);
}

function queueSpeech(delta) {
  if (!canSpeak()) return;
  state.speechBuffer += delta;
  const match = [...state.speechBuffer.matchAll(/[。！？!?](?:\s|$)*/g)].pop();
  if (!match || match.index === undefined) return;
  const end = match.index + match[0].length;
  speak(state.speechBuffer.slice(0, end));
  state.speechBuffer = state.speechBuffer.slice(end);
}

function flushSpeech() {
  if (state.speechBuffer.trim()) speak(state.speechBuffer);
  state.speechBuffer = "";
}

function stopSpeaking() {
  state.speechBuffer = "";
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}

function replayLastAnswer() {
  const message = [...state.messages].reverse().find((item) => item.role === "assistant" && item.content.trim());
  if (!message) return;
  stopSpeaking();
  if (!("speechSynthesis" in window)) {
    setError("当前浏览器不支持语音朗读");
    return;
  }
  const utterance = new SpeechSynthesisUtterance(message.content);
  utterance.lang = "zh-CN";
  utterance.rate = 1.03;
  window.speechSynthesis.speak(utterance);
}

function supportedRecordingType() {
  if (!("MediaRecorder" in window)) return "";
  for (const type of ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"]) {
    if (!MediaRecorder.isTypeSupported || MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

async function toggleRecording() {
  if (state.recorder?.state === "recording") {
    stopRecording();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !("MediaRecorder" in window)) {
    setError("当前浏览器不支持录音，请使用文字提问");
    return;
  }
  setError("");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = supportedRecordingType();
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    state.recorder = recorder;
    state.recordingStream = stream;
    state.recordingChunks = [];
    state.recordingMimeType = recorder.mimeType || mimeType || "audio/webm";
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size) state.recordingChunks.push(event.data);
    });
    recorder.addEventListener("stop", finishRecording, { once: true });
    recorder.start();
    state.recordingTimer = window.setTimeout(stopRecording, MAX_RECORDING_MS);
    setStatus("录音中，点击红点结束");
    renderControls();
  } catch {
    setError("无法使用麦克风，请确认浏览器已授权后重试");
    stopRecordingResources();
  }
}

function stopRecording() {
  if (state.recorder?.state === "recording") state.recorder.stop();
}

function stopRecordingResources() {
  if (state.recordingTimer) window.clearTimeout(state.recordingTimer);
  state.recordingTimer = null;
  state.recordingStream?.getTracks().forEach((track) => track.stop());
  state.recordingStream = null;
  state.recorder = null;
  renderControls();
}

async function finishRecording() {
  const type = state.recordingMimeType || "audio/webm";
  const chunks = state.recordingChunks;
  stopRecordingResources();
  setStatus("正在识别语音");
  if (!chunks.length) {
    setError("没有录到有效声音，请重试");
    return;
  }
  try {
    const blob = new Blob(chunks, { type });
    const extension = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("file", new File([blob], `voice-recording.${extension}`, { type }));
    const text = await api("/api/ai-chat/transcription", { method: "POST", body: form });
    const recognized = String(text.text || "").trim();
    if (!recognized) throw new Error("未识别到有效语音内容");
    setStatus("语音已识别，正在发送");
    await sendMessage(null, recognized);
  } catch (error) {
    setError(error.message || "语音转写失败");
    setStatus("已关联本设备自选基金，可语音或文字咨询");
  }
}

els.loginForm.addEventListener("submit", login);
els.composer.addEventListener("submit", sendMessage);
els.stopBtn.addEventListener("click", stopGeneration);
els.recordBtn.addEventListener("click", toggleRecording);
els.replayBtn.addEventListener("click", replayLastAnswer);
els.stopSpeakBtn.addEventListener("click", stopSpeaking);
els.messageInput.addEventListener("input", resizeInput);
els.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.composer.requestSubmit();
  }
});
els.autoSpeakToggle.checked = state.autoSpeak;
els.autoSpeakToggle.addEventListener("change", () => {
  state.autoSpeak = els.autoSpeakToggle.checked;
  saveAutoSpeak(state.autoSpeak);
  if (!state.autoSpeak) stopSpeaking();
});

renderControls();
loadStatus();
