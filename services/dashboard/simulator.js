(function () {
  const $ = id => document.getElementById(id);
  let users = [], languages = {}, session, socket, enrollment, preview, previewUrl;
  let selectedUser = "", recording = false, uploading = false, chatting = false, opening = false;
  let savedSteps = new Set(), editingStep = 0;
  let stopRecording, toastTimer, expiryTimer;
  const errors = {no_speech:"音量太小或沒有收到聲音，請靠近麥克風重錄。", too_short:"錄音不足 3 秒，請完整朗讀後重錄。", clipping:"音量過大造成爆音，請離麥克風稍遠。", audio_too_long:"錄音超過 20 秒，請縮短後重錄。", enrollment_expired:"登記已逾時，請取消後重新開始。", user_not_found:"使用者已被刪除，請返回列表。", user_disabled:"使用者已停用。"};
  const message = e => errors[e.message] || e.message;
  function toast(text) {
    clearTimeout(toastTimer); $("toast").textContent = text; $("toast").style.display = "block";
    toastTimer = setTimeout(() => {$("toast").style.display = "none";}, 4500);
  }
  async function api(path, options = {}) {
    const r = await fetch(path, {...options, headers:{...options.headers, ...(options.method && options.method !== "GET" ? {"X-CSRF-Token": window.__iotCsrf || ""} : {})}});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (r.status === 401) location.href = "/dashboard";
      throw Error(typeof data.detail === "string" ? data.detail : "請檢查輸入內容或連線");
    }
    return data;
  }
  function current() {return users.find(u => u.user_id === selectedUser);}
  function language() {return current()?.preferred_language || "yue-HK";}
  function voiceFor(code) {
    const expected = languages[code]?.tts || code;
    return window.speechSynthesis?.getVoices().find(v => {
      const lang = v.lang.replace("_","-").toLowerCase();
      return code === "yue-HK" ? ["zh-hk","yue-hk","yue"].includes(lang) : code === "zh-CN" ? ["zh-cn","zh-tw","zh"].includes(lang) : lang.startsWith("en");
    });
  }
  function voiceStatus() {
    const name = languages[language()]?.label || language();
    $("voice-status").textContent = voiceFor(language()) ? "語音回覆：" + name + "（瀏覽器音色）" : "本機未提供" + name + "音色；可查看字幕，請安裝對應系統語音後重開瀏覽器。";
  }
  function identifiedUser(text, kind = "known") {
    screen?.setUser(text);
  }
  function speak(text, code) {
    const voice = voiceFor(code);
    if (!voice) {toast("沒有此語言的語音音色，請按文字朗讀；回覆仍可查看字幕。"); return;}
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice; utterance.lang = voice.lang;
    utterance.onerror = () => toast("語音播放失敗，請查看字幕並檢查瀏覽器音訊設定。");
    speechSynthesis.speak(utterance);
  }
  function controls() {
    const locked = !!enrollment || recording || uploading || chatting || opening;
    $("user-select").disabled = locked;
    $("new-user").disabled = locked;
    $("enroll-language").disabled = locked;
    $("enroll-start").disabled = locked || !selectedUser || !session;
    $("conversation-record").disabled = !!enrollment || uploading || opening || !session || (chatting && !recording);
    $("enroll-record").disabled = uploading || (!stopRecording && recording);
    $("enroll-confirm").disabled = recording || uploading || (!preview && savedSteps.size !== 3);
    $("enroll-cancel").disabled = recording || uploading;
    $("read-example").disabled = recording || uploading;
    $("stop-playback").disabled = !chatting;
    const nextStep = [1,2,3].find(step => !savedSteps.has(step));
    document.querySelectorAll("#step-buttons button").forEach(button => {
      const step = Number(button.dataset.step);
      button.disabled = !enrollment || recording || uploading || (!savedSteps.has(step) && step !== nextStep);
    });
  }
  function clearPreview() {
    $("sample-preview").pause();
    $("sample-preview").removeAttribute("src"); $("sample-preview").hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    preview = previewUrl = null;
  }
  function activeStep() { return editingStep || ([1,2,3].find(s => !savedSteps.has(s)) || 1); }
  function stepView() {
    const count = savedSteps.size, step = activeStep();
    $("sample-count").textContent = count + " / 3";
    $("progress-bar").style.width = count / 3 * 100 + "%";
    $("step-title").textContent = count === 3 ? "三段已保存" : "第 " + step + " / 3 步";
    $("reading-prompt").textContent = enrollment?.prompts[step-1] || "";
    $("enroll-confirm").textContent = count === 3 ? "確認並完成／更新登記" : "確認並保存第 " + step + " 段";
    $("enroll-record").textContent = preview ? "重新錄音" : "● 開始錄音";
    document.querySelectorAll("#step-buttons button").forEach(b => {
      const s = Number(b.dataset.step);
      b.classList.toggle("done", savedSteps.has(s));
      b.classList.toggle("active", s === step);
      b.setAttribute("aria-pressed", String(s === step));
    });
    controls();
  }
  async function loadUsers() {
    users = [];
    let page = 1, result;
    do {
      result = await api("/api/users?status=active&page_size=100&page=" + page++);
      users.push(...result.items);
    } while (users.length < result.total && result.items.length);
    $("user-select").replaceChildren(new Option("請選擇", ""));
    for (const u of users) $("user-select").add(new Option(u.display_name || u.user_id, u.user_id));
    if (!current()) selectedUser = "";
    $("user-select").value = selectedUser;
    userView();
  }
  function userView() {
    $("user-language").textContent = selectedUser ? "偏好對話語言：" + (languages[language()]?.label || language()) : "";
    $("user-detail").hidden = !selectedUser;
    $("user-detail").href = "/dashboard/user/" + encodeURIComponent(selectedUser);
    $("enroll-language").value = language();
    voiceStatus(); controls();
  }
  async function savePreference() {
    try { await api("/api/simulator/preferences", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({selected_user_id: selectedUser})}); } catch(e) {}
  }
  async function refreshSamples() {
    if(!selectedUser) return;
    const userId = selectedUser;
    try {
      const samples = await api("/api/users/"+encodeURIComponent(userId)+"/voiceprint/samples");
      if(userId !== selectedUser || enrollment || opening) return;
      savedSteps = new Set(samples.map(s => s.step));
      $("enrollment-status").textContent = savedSteps.size ? "此使用者已保存 " + savedSteps.size + " / 3 段聲紋。" : "先選擇使用者。";
      stepView();
    } catch(e) {}
  }
  async function startEnrollment() {
    if (!selectedUser || opening || enrollment) return;
    opening = true; controls(); clearPreview(); window.speechSynthesis?.cancel();
    try {
      enrollment = await api("/api/enrollments", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({user_id:selectedUser, language:$("enroll-language").value})});
      savedSteps = new Set(enrollment.saved_steps || []);
      editingStep = 0;
      $("wizard").hidden = false;
      $("enrollment-status").textContent = savedSteps.size ? "已保存 " + savedSteps.size + " 段，可繼續或點選任一段重錄。" : "請朗讀第 1 段；錄完試聽，確認後才會保存。";
      $("record-status").textContent = "";
      expiryTimer = setTimeout(() => {$("enrollment-status").textContent = "登記已逾時，請取消並重新開始。"; toast("登記已逾時");}, Math.max(0, enrollment.expires_at*1000-Date.now()));
    } catch(e) {toast(message(e));}
    finally {opening = false; stepView();}
  }
  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length*2), view = new DataView(buffer);
    const write = (offset,text) => [...text].forEach((c,i)=>view.setUint8(offset+i,c.charCodeAt(0)));
    write(0,"RIFF"); view.setUint32(4,36+samples.length*2,true); write(8,"WAVE"); write(12,"fmt ");
    view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
    view.setUint32(24,sampleRate,true); view.setUint32(28,sampleRate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
    write(36,"data"); view.setUint32(40,samples.length*2,true);
    samples.forEach((s,i)=>view.setInt16(44+i*2,Math.max(-1,Math.min(1,s))*(s<0?32768:32767),true));
    return new Blob([buffer],{type:"audio/wav"});
  }
  async function recordWav(maxMs, kind, epoch) {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) throw Error("請用 Chrome 或 Edge，並允許麥克風。");
    let stream, context, source, processor, sink, timer, clock;
    const chunks = [];
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
      if(kind==="chat" && screen.epoch!==epoch)throw Error("recording_cancelled");
      if(kind==="chat") screen.setState("listening", "請說出你現在的感受…");
      context = new AudioContext(); await context.resume();
      if(kind==="chat" && screen.epoch!==epoch)throw Error("recording_cancelled");
      source = context.createMediaStreamSource(stream); processor = context.createScriptProcessor(4096,1,1); sink = context.createGain(); sink.gain.value=0;
      processor.onaudioprocess = e => {
        const data=new Float32Array(e.inputBuffer.getChannelData(0)); chunks.push(data);
        $("mic-level").value = Math.min(1,Math.sqrt(data.reduce((n,x)=>n+x*x,0)/data.length)*4);
        if(kind==="chat") screen.setLevel($("mic-level").value);
      };
      source.connect(processor); processor.connect(sink); sink.connect(context.destination);
      const started=Date.now();
      await new Promise(resolve => {
        stopRecording=resolve; timer=setTimeout(resolve,maxMs);
        clock=setInterval(()=>{$("record-status").textContent="錄音中 · "+((Date.now()-started)/1000).toFixed(1)+" 秒";},100);
        controls();
      });
      const samples=new Float32Array(chunks.reduce((n,x)=>n+x.length,0)); let offset=0;
      for(const chunk of chunks){samples.set(chunk,offset);offset+=chunk.length;}
      if(samples.length/context.sampleRate < 3) throw Error("too_short");
      return encodeWav(samples,context.sampleRate);
    } finally {
      clearTimeout(timer); clearInterval(clock); stopRecording=null;
      source?.disconnect(); processor?.disconnect(); sink?.disconnect();
      stream?.getTracks().forEach(track=>track.stop()); if(context) await context.close();
      $("mic-level").value=0;
      if(kind==="chat") screen.setLevel(0);
    }
  }
  async function record(kind) {
    if(recording){stopRecording?.();return;}
    if(uploading || chatting || opening) return;
    recording=true; controls(); window.speechSynthesis?.cancel(); $("sample-preview").pause();
    if(kind==="chat"){screen.begin("pc-"+Date.now()+"-"+Math.random().toString(36).slice(2));screen.setState("idle","正在請求麥克風權限…");}
    const epoch=screen.epoch;
    const button=kind==="enroll"?$("enroll-record"):$("conversation-record");
    button.textContent="■ 停止錄音";
    let blob;
    try {blob=await recordWav(kind==="enroll"?20000:30000,kind,epoch);}
    catch(e){if(kind!=="chat" || screen.epoch===epoch){$("record-status").textContent=message(e);toast(message(e));if(kind==="chat")screen.fail(message(e));}}
    finally{recording=false;button.textContent=kind==="enroll"?"重新錄音":"● 開始語音對話";controls();}
    if(!blob || (kind==="chat" && screen.epoch!==epoch)) return;
    if(kind==="enroll"){
      clearPreview(); preview=blob; previewUrl=URL.createObjectURL(blob);
      $("sample-preview").src=previewUrl;$("sample-preview").hidden=false;
      $("record-status").textContent="錄音完成，請試聽；滿意後按確認，或重新錄音。";stepView();
    } else {
      chatting=true;controls();$("turn-status").textContent="語音辨識中…";
      screen.setState("transcribing");
      try{
        const form=new FormData();form.append("file",blob,"turn.wav");
        const identity=await api("/api/voiceprint/identify",{method:"POST",body:form});
        if(identity.decision!=="accepted") {
            identifiedUser(identity.decision==="ambiguous"?"未錄入（無法唯一確認）":"未錄入","unknown");
          throw Error(identity.decision==="ambiguous"?"聲紋無法唯一辨識，請重新錄音。":"聲紋庫中沒有此使用者，請先完成聲紋登記。");
        }
        selectedUser=identity.user_id;
        const identified=users.find(u=>u.user_id===identity.user_id);
        identifiedUser(identified?.display_name || identity.user_id);
        $("user-select").value=selectedUser;
        $("turn-status").textContent="聲紋已確認，語音辨識中…";
        // Reload the profile so a preference edited in another page applies.
        const fresh=await api("/api/users/"+encodeURIComponent(identity.user_id));
        users=users.map(u=>u.user_id===identity.user_id?fresh:u);voiceStatus();
        if(!$("asr-auto").checked) form.append("language",languages[language()].asr);
        const transcript=await api("/api/transcribe",{method:"POST",body:form});
        if(screen.epoch!==epoch)return;
        if(!transcript.text?.trim())throw Error("沒有辨識到語音，請重試。");
        if(!socket || socket.readyState!==WebSocket.OPEN)throw Error("連線已中斷，請重新整理頁面。");
        $("captions").textContent=transcript.text;$("turn-status").textContent="等待回覆…";
        socket.send(JSON.stringify({type:"chat",request_id:screen.requestId,text:transcript.text,user_id:identity.user_id,voiceprint_id:identity.template_id,device_id:session.device_id}));
      }catch(e){if(screen.epoch!==epoch)return;chatting=false;screen.fail(message(e));$("turn-status").textContent=message(e);toast(message(e));controls();}
    }
  }
  async function confirmSample() {
    if(!enrollment || uploading || recording || (!preview && savedSteps.size!==3))return;
    uploading=true;controls();
    try {
      if(preview){
        const step=activeStep();
        const form=new FormData(); form.append("file",preview,"sample.wav");form.append("step",String(step));
        const result=await api("/api/enrollments/"+enrollment.enrollment_id+"/samples",{method:"POST",body:form});
        savedSteps.add(step);clearPreview();editingStep=0;
        $("enrollment-status").textContent=result.sample_count<3?"第 "+step+" 段已保存（共 "+result.sample_count+" 段）。":"三段已保存，可完成／更新模板。";
        toast("第 "+step+" 段已保存");
        $("record-status").textContent="";
      }
      if(savedSteps.size===3){
        await api("/api/enrollments/"+enrollment.enrollment_id+"/complete",{method:"POST"});
        clearTimeout(expiryTimer);enrollment=null;$("wizard").hidden=true;
        $("enrollment-status").textContent="聲紋模板已建立／更新！可再按「開始登記」重錄任一段。";
        $("enroll-start").textContent="重新登記／修改";
        toast("聲紋模板已更新。");
      }
    }catch(e){$("enrollment-status").textContent=message(e);toast(message(e));}
    finally{uploading=false;stepView();}
  }
  async function cancelEnrollment(){
    if(recording || uploading || !enrollment)return;
    uploading=true;controls();
    try {
      await api("/api/enrollments/"+enrollment.enrollment_id,{method:"DELETE"});
      enrollment=null;clearTimeout(expiryTimer);clearPreview();savedSteps=new Set();editingStep=0;$("wizard").hidden=true;
      $("enrollment-status").textContent="已取消登記，已保存的聲紋段落保持不變。";toast("已取消登記");
    }catch(e){toast(message(e));}
    finally{uploading=false;stepView();}
  }
  const play=window.EspDisplay.createSpeechPlayer({
    voiceFor:code=>voiceFor(code||language()),speech:window.speechSynthesis,
    Utterance:window.SpeechSynthesisUtterance
  });
  const screen=window.EspDisplay.mount($("display"),{
    status:$("display-state"),progress:$("segment-progress"),transcript:$("captions"),play,
    onComplete:()=>{chatting=false;$("turn-status").textContent="本輪播放／字幕預覽完成";controls();}
  });
  async function initialize(){
    try{
      const auth=await api("/api/auth/session");window.__iotCsrf=auth.csrf_token;
      languages=await api("/api/enrollment-languages");
      selectedUser=new URLSearchParams(location.search).get("user")||"";
      if(!selectedUser){
        const preference=await api("/api/simulator/preferences");
        selectedUser=preference.selected_user_id||"";
      }
      await loadUsers();await refreshSamples();
      session=await api("/api/simulator/sessions",{method:"POST"});
      socket=new WebSocket((location.protocol==="https:"?"wss":"ws")+"://"+location.host+"/ws/simulator/"+session.session_id);
      socket.onopen=()=>{screen.begin();identifiedUser("未錄入","unknown");$("connection").textContent="語音連線已就緒";$("connection").className="state ok";$("turn-status").textContent="請開始說話，系統會自動辨識使用者";controls();};
      socket.onclose=()=>{stopRecording?.();screen.offline();session=null;chatting=false;$("connection").textContent="連線中斷，請重新整理";$("connection").className="state error";controls();};
      socket.onerror=()=>{screen.offline("WebSocket 連線失敗，請重新整理");toast("WebSocket 連線失敗，請檢查後端。");};
      socket.onmessage=event=>{
        let m;try{m=JSON.parse(event.data);}catch(_){toast("收到無法辨識的裝置事件");return;}
        if(!chatting)return;
        if(m.type==="turn.started" && screen.requestId!==m.payload?.request_id)return;
        if(m.type!=="turn.started" && m.turn_id && m.turn_id!==screen.turn)return;
        screen.event(m);
        if(m.type==="turn.failed"){chatting=false;$("turn-status").textContent=m.payload.error;toast(m.payload.error);controls();}
        if(m.type==="tts.end" && chatting){$("turn-status").textContent="回覆已接收，等待本機播放完成…";}

      };
    }catch(e){screen.offline("初始化失敗，請檢查登入及服務");$("connection").textContent="初始化失敗";toast(message(e));}
  }
  $("user-select").onchange=e=>{screen.begin();selectedUser=e.target.value;clearPreview();savedSteps=new Set();editingStep=0;$("enrollment-status").textContent="準備開始新的三步登記。";stepView();userView();savePreference();refreshSamples();};
  $("new-user").onclick=()=>{$("profile-form").hidden=!$("profile-form").hidden;};
  $("profile-form").onsubmit=async e=>{
    e.preventDefault();if(uploading)return;uploading=true;controls();
    const button=e.submitter;button.disabled=true;
    try{
      const body=Object.fromEntries(new FormData(e.target));
      body.age_at_registration=body.age_at_registration===""?null:Number(body.age_at_registration);
      const user=await api("/api/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      selectedUser=user.user_id;await loadUsers();$("profile-form").hidden=true;e.target.reset();
      toast("使用者已建立，請確認朗讀語言並開始三步登記。");
    }catch(e){toast(message(e));}finally{uploading=false;button.disabled=false;controls();}
  };
  $("enroll-start").onclick=startEnrollment;
  document.querySelectorAll("#step-buttons button").forEach(button => {
    button.onclick=()=>{
      if(button.disabled || !enrollment || recording || uploading)return;
      editingStep=Number(button.dataset.step);clearPreview();stepView();
    };
  });
  $("enroll-record").onclick=()=>record("enroll");
  $("enroll-confirm").onclick=confirmSample;
  $("enroll-cancel").onclick=cancelEnrollment;
  $("conversation-record").onclick=()=>record("chat");
  $("stop-playback").onclick=()=>{
    screen.fail("本輪已停止。可重新開始對話。");chatting=false;
    $("turn-status").textContent="已停止本機播放／字幕；已送出的後端請求不會撤回";controls();
  };
  $("read-example").onclick=()=>{window.speechSynthesis?.cancel();speak($("reading-prompt").textContent,enrollment.language);};
  window.speechSynthesis?.addEventListener("voiceschanged",voiceStatus);
  window.addEventListener("pagehide",()=>{screen.dispose();stopRecording?.();window.speechSynthesis?.cancel();clearPreview();socket?.close();});
  initialize();
})();
