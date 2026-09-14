(function () {
  const $ = (id) => document.getElementById(id);
  let session = null;
  let selectedUser = "";
  let enrollment = null;
  let sampleCount = 0;
  let socket = null;

  const csrf = () => window.__iotCsrf || "";
  const toast = (message) => {
    const el = $("toast");
    el.textContent = message;
    el.style.display = "block";
    setTimeout(() => { el.style.display = "none"; }, 3500);
  };

  async function api(path, options = {}) {
    options.headers = {
      ...(options.headers || {}),
      ...(options.method && options.method !== "GET" ? { "X-CSRF-Token": csrf() } : {}),
    };
    const response = await fetch(path, options);
    if (!response.ok) {
      throw new Error((await response.json().catch(() => ({}))).detail || String(response.status));
    }
    return response.json();
  }

  function draw(text = "準備就緒", mood = "neutral") {
    const canvas = $("display");
    const context = canvas.getContext("2d");
    context.fillStyle = "#152137";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = mood === "negative" ? "#ff9b9b" : "#8de0c3";
    context.font = "26px system-ui";
    context.textAlign = "center";
    context.fillText(mood === "negative" ? "…" : "♡", 160, 90);
    context.fillStyle = "#eef3f8";
    context.font = "16px system-ui";
    context.fillText(String(text).slice(0, 36), 160, 145);
  }

  async function loadUsers() {
    const data = await api("/api/users");
    const select = $("user-select");
    select.innerHTML = "<option value=\"\">請選擇</option>" +
      data.items.map((user) => `<option value="${user.user_id}">${user.display_name || user.user_id}</option>`).join("");
  }

  async function createSession() {
    session = await api("/api/simulator/sessions", { method: "POST" });
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/simulator/${session.session_id}`);
    socket.onopen = () => { $("connection").textContent = "Session / WebSocket 已連線"; };
    socket.onclose = () => {
      $("connection").textContent = "WebSocket 已中斷";
      $("connection").className = "state error";
    };
    socket.onerror = () => { $("connection").className = "state error"; };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "stt.final") $("captions").textContent = message.payload.text;
      if (message.type === "tts.segment") {
        $("captions").textContent += `　→　${message.payload.text}`;
        draw(message.payload.text, message.payload.result?.emotion);
      }
      if (message.type === "turn.failed") toast(`對話錯誤：${message.payload.error}`);
      if (message.type === "tts.end") {
        const result = message.payload.result || {};
        $("turn-status").textContent = `${result.emotion || "neutral"} · ${result.latency_ms?.total || 0} ms`;
      }
    };
    $("connection").textContent = "Session 已連線";
    $("connection").className = "state ok";
    $("conversation-record").disabled = false;
    $("enroll-start").disabled = false;
    $("turn-status").textContent = "可以開始測試";
    draw();
  }

  async function startEnrollment() {
    if (!selectedUser) { toast("請先選擇使用者"); return; }
    enrollment = await api("/api/enrollments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: selectedUser }),
    });
    sampleCount = 0;
    $("enroll-record").disabled = false;
    $("enrollment-status").textContent = "按下按鈕朗讀第 1 段短句";
    $("enroll-start").disabled = true;
  }

  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeString = (offset, value) => {
      for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
    };
    writeString(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, samples.length * 2, true);
    samples.forEach((sample, index) => {
      const clamped = Math.max(-1, Math.min(1, sample));
      view.setInt16(44 + index * 2, clamped < 0 ? clamped * 32768 : clamped * 32767, true);
    });
    return new Blob([buffer], { type: "audio/wav" });
  }

  async function recordWav(durationMs) {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
      throw new Error("此瀏覽器不支援 PCM 麥克風錄音，請使用最新版 Chrome 或 Edge");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    const sink = context.createGain();
    const chunks = [];
    sink.gain.value = 0;
    processor.onaudioprocess = (event) => {
      chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };
    source.connect(processor);
    processor.connect(sink);
    sink.connect(context.destination);
    await new Promise((resolve) => setTimeout(resolve, durationMs));
    source.disconnect();
    processor.disconnect();
    sink.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    const sampleRate = context.sampleRate;
    await context.close();
    const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
    const samples = new Float32Array(length);
    let offset = 0;
    chunks.forEach((chunk) => { samples.set(chunk, offset); offset += chunk.length; });
    if (!samples.length) throw new Error("沒有收到麥克風音訊，請確認輸入裝置");
    return encodeWav(samples, sampleRate);
  }

  async function submitSample(kind, blob) {
    $("sample-preview").src = URL.createObjectURL(blob);
    $("sample-preview").hidden = false;
    if (kind === "enroll") {
      const form = new FormData();
      form.append("file", blob, "sample.wav");
      const result = await api(`/api/enrollments/${enrollment.enrollment_id}/samples`, { method: "POST", body: form });
      sampleCount = result.sample_count || result.samples || sampleCount + 1;
      $("sample-count").textContent = `${sampleCount} / 3`;
      $("progress-bar").style.width = `${Math.min(100, sampleCount / 3 * 100)}%`;
      if (sampleCount >= 3) {
        await api(`/api/enrollments/${enrollment.enrollment_id}/complete`, { method: "POST" });
        $("enrollment-status").textContent = "聲紋登記完成，可以開始對話";
        $("enroll-record").disabled = true;
        $("conversation-record").disabled = false;
      } else {
        $("enrollment-status").textContent = `已收到樣本，請錄製第 ${sampleCount + 1} 段`;
      }
      return;
    }
    const form = new FormData();
    form.append("file", blob, "turn.wav");
    const transcript = await api("/api/transcribe", { method: "POST", body: form });
    const text = transcript.text || "";
    if (!text) { toast("沒有辨識到語音，請再試一次"); return; }
    if (socket && socket.readyState === WebSocket.OPEN) {
      $("turn-status").textContent = "處理中…";
      socket.send(JSON.stringify({ type: "chat", text, user_id: selectedUser || "sim-user", device_id: session.device_id }));
      return;
    }
    const reply = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: selectedUser || "sim-user", text, device_id: session.device_id }),
    });
    $("captions").textContent = `${text}　→　${reply.reply}`;
    draw(reply.reply, reply.emotion);
    $("turn-status").textContent = `${reply.emotion} · ${reply.latency_ms} ms`;
  }

  async function record(kind) {
    try {
      const duration = kind === "enroll" ? 5000 : 8000;
      const blob = await recordWav(duration);
      await submitSample(kind, blob);
    } catch (error) {
      toast(`${kind === "enroll" ? "聲紋" : "對話"}錄音失敗：${error.message}`);
    }
  }

  $("user-select").addEventListener("change", (event) => {
    selectedUser = event.target.value;
    $("enroll-start").disabled = !selectedUser || !session;
  });
  $("new-user").addEventListener("click", () => { $("profile-form").hidden = !$("profile-form").hidden; });
  $("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const body = Object.fromEntries(new FormData(event.target).entries());
      if (body.age_at_registration) body.age_at_registration = Number(body.age_at_registration);
      const user = await api("/api/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await loadUsers();
      $("user-select").value = user.user_id;
      selectedUser = user.user_id;
      $("profile-form").hidden = true;
      await startEnrollment();
    } catch (error) { toast(`建立使用者失敗：${error.message}`); }
  });
  $("enroll-start").addEventListener("click", startEnrollment);
  $("enroll-record").addEventListener("click", () => record("enroll"));
  $("conversation-record").addEventListener("click", () => record("chat"));
  window.addEventListener("iot:authenticated", async () => {
    try { await loadUsers(); await createSession(); }
    catch (error) { toast(`模擬器初始化失敗：${error.message}`); }
  });
  if (document.body.dataset.authenticated === "true") createSession();
  draw();
})();
