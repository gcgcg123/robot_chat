(function () {
  const $ = id => document.getElementById(id);
  let users = [], languages = {}, session, socket, enrollment, preview, previewUrl;
  let selectedUser = "", saved = 0, recording = false, uploading = false, chatting = false, opening = false;
  let stopRecording, toastTimer, expiryTimer;
  const errors = {no_speech:"音量太小或没有收到声音，请靠近麦克风重录。", too_short:"录音不足 3 秒，请完整朗读后重录。", clipping:"音量过大造成爆音，请离麦克风稍远。", audio_too_long:"录音超过 20 秒，请缩短后重录。", enrollment_expired:"登记已逾时，请取消后重新开始。", user_not_found:"用户已被删除，请返回列表。", user_disabled:"用户已停用。"};
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
      throw Error(typeof data.detail === "string" ? data.detail : "请检查输入内容或连接");
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
    $("voice-status").textContent = voiceFor(language()) ? "语音回复：" + name + "（浏览器音色）" : "本机未提供" + name + "音色；可查看字幕，请安装对应系统语音后重开浏览器。";
  }
  function speak(text, code) {
    const voice = voiceFor(code);
    if (!voice) {toast("没有此语言的语音音色，请按文字朗读；回复仍可查看字幕。"); return;}
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice; utterance.lang = voice.lang;
    utterance.onerror = () => toast("语音播放失败，请查看字幕并检查浏览器音频设定。");
    speechSynthesis.speak(utterance);
  }
  function controls() {
    const locked = !!enrollment || recording || uploading || chatting || opening;
    $("user-select").disabled = locked;
    $("new-user").disabled = locked;
    $("enroll-language").disabled = locked;
    $("enroll-start").disabled = locked || !selectedUser || !session;
    $("conversation-record").disabled = !!enrollment || uploading || opening || !selectedUser || !session || (chatting && !recording);
    $("enroll-record").disabled = uploading || saved === 3 || (!stopRecording && recording);
    $("enroll-confirm").disabled = recording || uploading || (!preview && saved !== 3);
    $("enroll-cancel").disabled = recording || uploading;
    $("read-example").disabled = recording || uploading;
    $("stop-playback").disabled = !chatting;
  }
  function clearPreview() {
    $("sample-preview").pause();
    $("sample-preview").removeAttribute("src"); $("sample-preview").hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    preview = previewUrl = null;
  }
  function stepView() {
    $("sample-count").textContent = saved + " / 3";
    $("progress-bar").style.width = saved / 3 * 100 + "%";
    $("step-title").textContent = saved === 3 ? "三段已保存" : "第 " + (saved+1) + " / 3 步";
    $("reading-prompt").textContent = enrollment?.prompts[Math.min(saved,2)] || "";
    $("enroll-confirm").textContent = saved >= 2 ? "确认并完成登记" : "确认并继续";
    $("enroll-record").textContent = preview ? "重新录音" : "● 开始录音";
    controls();
  }
  async function loadUsers() {
    users = [];
    let page = 1, result;
    do {
      result = await api("/api/users?status=active&page_size=100&page=" + page++);
      users.push(...result.items);
    } while (users.length < result.total && result.items.length);
    $("user-select").replaceChildren(new Option("请选择", ""));
    for (const u of users) $("user-select").add(new Option(u.display_name || u.user_id, u.user_id));
    if (!current()) selectedUser = "";
    $("user-select").value = selectedUser;
    userView();
  }
  function userView() {
    $("user-language").textContent = selectedUser ? "偏好对话语言：" + (languages[language()]?.label || language()) : "";
    $("user-detail").hidden = !selectedUser;
    $("user-detail").href = "/dashboard/user/" + encodeURIComponent(selectedUser);
    $("enroll-language").value = language();
    voiceStatus(); controls();
  }
  async function startEnrollment() {
    if (!selectedUser || opening || enrollment) return;
    opening = true; controls(); clearPreview(); window.speechSynthesis?.cancel();
    try {
      enrollment = await api("/api/enrollments", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({user_id:selectedUser, language:$("enroll-language").value})});
      saved = 0;
      $("wizard").hidden = false;
      $("enrollment-status").textContent = "请朗读第 1 段；录完试听，确认后才会保存。";
      $("record-status").textContent = "";
      expiryTimer = setTimeout(() => {$("enrollment-status").textContent = "登记已逾时，请取消并重新开始。"; toast("登记已逾时");}, Math.max(0, enrollment.expires_at*1000-Date.now()));
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
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) throw Error("请用 Chrome 或 Edge，并允许麦克风。");
    let stream, context, source, processor, sink, timer, clock;
    const chunks = [];
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
      if(kind==="chat" && screen.epoch!==epoch)throw Error("recording_cancelled");
      if(kind==="chat") screen.setState("listening", "请说出你现在的感受…");
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
        clock=setInterval(()=>{$("record-status").textContent="录音中 · "+((Date.now()-started)/1000).toFixed(1)+" 秒";},100);
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
    if(kind==="chat"){screen.begin("pc-"+Date.now()+"-"+Math.random().toString(36).slice(2));screen.setState("idle","正在请求麦克风权限…");}
    const epoch=screen.epoch;
    const button=kind==="enroll"?$("enroll-record"):$("conversation-record");
    button.textContent="■ 停止录音";
    let blob;
    try {blob=await recordWav(kind==="enroll"?20000:30000,kind,epoch);}
    catch(e){if(kind!=="chat" || screen.epoch===epoch){$("record-status").textContent=message(e);toast(message(e));if(kind==="chat")screen.fail(message(e));}}
    finally{recording=false;button.textContent=kind==="enroll"?"重新录音":"● 开始语音对话";controls();}
    if(!blob || (kind==="chat" && screen.epoch!==epoch)) return;
    if(kind==="enroll"){
      clearPreview(); preview=blob; previewUrl=URL.createObjectURL(blob);
      $("sample-preview").src=previewUrl;$("sample-preview").hidden=false;
      $("record-status").textContent="录音完成，请试听；满意后按确认，或重新录音。";stepView();
    } else {
      chatting=true;controls();$("turn-status").textContent="语音识别中…";
      screen.setState("transcribing");
      try{
        // Reload the profile so a preference edited in another page applies.
        const fresh=await api("/api/users/"+encodeURIComponent(selectedUser));
        users=users.map(u=>u.user_id===selectedUser?fresh:u);voiceStatus();
        const form=new FormData();form.append("file",blob,"turn.wav");
        if(!$("asr-auto").checked) form.append("language",languages[language()].asr);
        const transcript=await api("/api/transcribe",{method:"POST",body:form});
        if(screen.epoch!==epoch)return;
        if(!transcript.text?.trim())throw Error("没有识别到语音，请重试。");
        if(!socket || socket.readyState!==WebSocket.OPEN)throw Error("连接已中断，请重新整理页面。");
        $("captions").textContent=transcript.text;$("turn-status").textContent="等待回复…";
        socket.send(JSON.stringify({type:"chat",request_id:screen.requestId,text:transcript.text,user_id:selectedUser,device_id:session.device_id}));
      }catch(e){if(screen.epoch!==epoch)return;chatting=false;screen.fail(message(e));$("turn-status").textContent=message(e);toast(message(e));controls();}
    }
  }
  async function confirmSample() {
    if(!enrollment || uploading || recording || (!preview && saved!==3))return;
    uploading=true;controls();
    try {
      if(saved<3){
        const form=new FormData(); form.append("file",preview,"sample.wav");form.append("step",String(saved+1));
        const result=await api("/api/enrollments/"+enrollment.enrollment_id+"/samples",{method:"POST",body:form});
        saved=result.sample_count;clearPreview();
        $("enrollment-status").textContent=saved<3?"第 "+saved+" 段已保存，请朗读第 "+(saved+1)+" 段。":"三段已保存，正在建立声纹模板…";
        toast("第 "+saved+" 段已保存"+(saved<3?"，进入第 "+(saved+1)+" 步":""));
        $("record-status").textContent="";
      }
      if(saved===3){
        await api("/api/enrollments/"+enrollment.enrollment_id+"/complete",{method:"POST"});
        clearTimeout(expiryTimer);enrollment=null;$("wizard").hidden=true;
        $("enrollment-status").textContent="三步登记完成！可开始模拟对话，或到个人资料页查看登记语言。";
        $("enroll-start").textContent="重新登记";
        toast("声纹登记完成，三段录音已确认。");await loadUsers();
      }
    }catch(e){$("enrollment-status").textContent=message(e);toast(message(e));}
    finally{uploading=false;stepView();}
  }
  async function cancelEnrollment(){
    if(recording || uploading || !enrollment)return;
    uploading=true;controls();
    try {
      await api("/api/enrollments/"+enrollment.enrollment_id,{method:"DELETE"});
      enrollment=null;clearTimeout(expiryTimer);clearPreview();saved=0;$("wizard").hidden=true;
      $("enrollment-status").textContent="已取消登记，原有声纹保持不变。";toast("已取消登记");
    }catch(e){toast(message(e));}
    finally{uploading=false;stepView();}
  }
  const play=window.EspDisplay.createSpeechPlayer({
    voiceFor:code=>voiceFor(code||language()),speech:window.speechSynthesis,
    Utterance:window.SpeechSynthesisUtterance
  });
  const screen=window.EspDisplay.mount($("display"),{
    status:$("display-state"),progress:$("segment-progress"),transcript:$("captions"),play,
    onComplete:()=>{chatting=false;$("turn-status").textContent="本轮播放／字幕预览完成";controls();}
  });
  async function initialize(){
    try{
      const auth=await api("/api/auth/session");window.__iotCsrf=auth.csrf_token;
      languages=await api("/api/enrollment-languages");
      selectedUser=new URLSearchParams(location.search).get("user")||"";await loadUsers();
      session=await api("/api/simulator/sessions",{method:"POST"});
      socket=new WebSocket((location.protocol==="https:"?"wss":"ws")+"://"+location.host+"/ws/simulator/"+session.session_id);
      socket.onopen=()=>{screen.begin();$("connection").textContent="语音连接已就绪";$("connection").className="state ok";$("turn-status").textContent="请选择用户后开始";controls();};
      socket.onclose=()=>{stopRecording?.();screen.offline();session=null;chatting=false;$("connection").textContent="连接中断，请重新整理";$("connection").className="state error";controls();};
      socket.onerror=()=>{screen.offline("WebSocket 连接失败，请重新整理");toast("WebSocket 连接失败，请检查后端。");};
      socket.onmessage=event=>{
        let m;try{m=JSON.parse(event.data);}catch(_){toast("收到无法识别的设备事件");return;}
        if(!chatting)return;
        if(m.type==="turn.started" && screen.requestId!==m.payload?.request_id)return;
        if(m.type!=="turn.started" && m.turn_id && m.turn_id!==screen.turn)return;
        screen.event(m);
        if(m.type==="turn.failed"){chatting=false;$("turn-status").textContent=m.payload.error;toast(m.payload.error);controls();}
        if(m.type==="tts.end" && chatting){$("turn-status").textContent="回复已接收，等待本机播放完成…";}

      };
    }catch(e){screen.offline("初始化失败，请检查登录及服务");$("connection").textContent="初始化失败";toast(message(e));}
  }
  $("user-select").onchange=e=>{screen.begin();selectedUser=e.target.value;clearPreview();saved=0;$("enrollment-status").textContent="准备开始新的三步登记。";stepView();userView();};
  $("new-user").onclick=()=>{$("profile-form").hidden=!$("profile-form").hidden;};
  $("profile-form").onsubmit=async e=>{
    e.preventDefault();if(uploading)return;uploading=true;controls();
    const button=e.submitter;button.disabled=true;
    try{
      const body=Object.fromEntries(new FormData(e.target));
      body.age_at_registration=body.age_at_registration===""?null:Number(body.age_at_registration);
      const user=await api("/api/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      selectedUser=user.user_id;await loadUsers();$("profile-form").hidden=true;e.target.reset();
      toast("用户已建立，请确认朗读语言并开始三步登记。");
    }catch(e){toast(message(e));}finally{uploading=false;button.disabled=false;controls();}
  };
  $("enroll-start").onclick=startEnrollment;
  $("enroll-record").onclick=()=>record("enroll");
  $("enroll-confirm").onclick=confirmSample;
  $("enroll-cancel").onclick=cancelEnrollment;
  $("conversation-record").onclick=()=>record("chat");
  $("stop-playback").onclick=()=>{
    screen.fail("本轮已停止。可重新开始对话。");chatting=false;
    $("turn-status").textContent="已停止本机播放／字幕；已送出的后端请求不会撤回";controls();
  };
  $("read-example").onclick=()=>{window.speechSynthesis?.cancel();speak($("reading-prompt").textContent,enrollment.language);};
  window.speechSynthesis?.addEventListener("voiceschanged",voiceStatus);
  window.addEventListener("pagehide",()=>{screen.dispose();stopRecording?.();window.speechSynthesis?.cancel();clearPreview();socket?.close();});
  initialize();
})();
