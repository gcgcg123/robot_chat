/* Project-owned 320x240 renderer and playback lifecycle; no upstream artwork. */
(function(root,factory){
  const api=factory();
  if(typeof module==="object" && module.exports) module.exports=api;
  else root.EspDisplay=api;
})(typeof window==="object"?window:globalThis,function(){
  "use strict";
  const labels={idle:"待機 · 我在這裡",listening:"正在聆聽",transcribing:"正在理解…",thinking:"正在思考",speaking:"正在回覆",offline:"裝置離線",error:"暫時無法完成"};
  function wrapText(text,measure,width){
    const lines=[];let line="";
    for(const char of Array.from(text)){
      if(char==="\n"){lines.push(line);line="";continue;}
      if(line && measure(line+char)>width){lines.push(line);line="";}
      line+=char;
    }
    if(line || !lines.length)lines.push(line);
    return lines;
  }
  class Display {
    constructor(options={}){
      this.now=options.now||(()=>Date.now());
      // Native Window timers cannot be invoked with a Display instance as `this`.
      this.later=options.later||((fn,ms)=>globalThis.setTimeout(fn,ms));
      this.clear=options.clear||(id=>globalThis.clearTimeout(id));
      this.play=options.play;this.onChange=options.onChange||(()=>{});this.onComplete=options.onComplete||(()=>{});
      this.epoch=0;this.reset("idle");
    }
    reset(state="idle",caption=""){
      this.epoch++;this.clear(this.hold);this.cancel?.();this.cancel=null;
      this.queue=[];this.active=false;this.done=false;this.completed=false;this.turn=null;this.seen=new Set();
      this.model={state,caption,transcript:"",userText:"",user:"未錄入",expression:"neutral",emotion:"neutral",risk:"none",level:0,progress:0,segment:0,mode:"",revealed:0,changedAt:this.now()};
      this.emit();
    }
    begin(requestId){this.reset();this.requestId=requestId;}
    emit(){this.onChange(this.model);}
    setLevel(level){this.model.level=Math.max(0,Math.min(1,level));}
    setUser(user){this.model.user=user||"未錄入";this.emit();}
    setState(state,caption){
      this.model.state=state;this.model.level=0;
      if(caption!==undefined)this.model.caption=caption;
      else if(state==="transcribing")this.model.caption="正在理解…";
      this.model.changedAt=this.now();this.emit();
    }
    offline(text){this.reset("offline",text||"連線中斷，請重新整理頁面");}
    fail(text){const turn=this.turn;this.reset("error",text||"處理失敗，請重新錄音");this.turn=turn;}
    event(event){
      const p=event.payload||{};
      if(event.type==="turn.started"){
        if(this.requestId && this.requestId!==p.request_id)return;
        if(!this.turn)this.turn=event.turn_id;return;
      }
      if(event.turn_id && event.turn_id!==this.turn)return;
      if(event.type==="turn.failed"){this.fail(p.error);return;}
      if(["offline","error"].includes(this.model.state))return;
      if(event.type==="stt.final"){this.model.userText=p.text||"";this.setState("thinking",p.text||"");}
      if(event.type==="display.state"){
        // Local recorder and playback own listening/speaking/idle, not network arrival.
        if(p.state==="thinking" && !this.active)this.setState("thinking");
        if(p.state==="error")this.fail(p.caption);
      }
      if(event.type==="tts.segment"){
        const key=p.segment_id??p.sequence??p.index;
        if(key!==undefined && this.seen.has(key))return;
        if(key!==undefined)this.seen.add(key);
        if(!p.text)return;
        this.queue.push(p);this.model.transcript+=p.text;this.pump();this.emit();
      }
      if(event.type==="tts.end"){this.done=true;this.finish();}
      if(event.type==="turn.failed")this.fail(p.error);
    }
    pump(){
      if(this.active || !this.queue.length)return;
      const p=this.queue.shift(),epoch=this.epoch;
      this.active=true;this.model.segment++;
      const emotion=p.result?.emotion, risk=p.result?.risk?.risk_level;
      if(emotion)this.model.emotion=emotion;
      if(risk)this.model.risk=risk;
      this.model.expression=this.model.risk==="urgent"?"urgent":this.model.emotion==="negative"?"comfort":this.model.emotion==="positive"?"happy":"neutral";
      this.model.caption="";this.model.progress=0;this.model.revealed=0;this.model.mode="等待音訊";
      this.setState("speaking");
      const segment=this.model.segment;
      const valid=()=>this.epoch===epoch && this.active && this.model.segment===segment;
      const reveal=(count,mode)=>{
        if(!valid())return;
        this.model.revealed=Math.max(this.model.revealed,Math.min(p.text.length,count));
        this.model.caption=p.text.slice(0,this.model.revealed);
        this.model.progress=this.model.revealed/p.text.length;
        if(mode)this.model.mode=mode;this.emit();
      };
      this.cancel=this.play(p.text,p.language,{
        start:audio=>{if(valid()){this.model.mode=audio?"播放進度（估算）":"字幕預覽（無音訊）";this.emit();}},
        boundary:count=>reveal(count,"播放進度（文字邊界）"),
        tick:count=>reveal(count),
        end:()=>{if(!valid())return;reveal(p.text.length);this.active=false;this.cancel=null;this.pump();this.finish();},
        error:()=>{if(valid()){this.model.mode="播放失敗 · 字幕預覽";this.emit();}}
      },p.media_url);
    }
    finish(){
      if(!this.done || this.active || this.queue.length || this.completed)return;
      this.completed=true;this.setState("idle");this.onComplete();
      const epoch=this.epoch;
      this.hold=this.later(()=>{if(this.epoch!==epoch)return;this.model.caption="";this.model.progress=0;this.model.mode="";this.emit();},5000);
    }
  }
  function createSpeechPlayer(options){
    const now=options.now||(()=>Date.now()), every=options.setInterval||setInterval, stop=options.clearInterval||clearInterval;
    const later=options.setTimeout||setTimeout, clear=options.clearTimeout||clearTimeout;
    return (text,language,hooks,mediaUrl)=>{
      let ended=false,started=false,audio=false,boundaries=false,timer,watchdog,startAt=now(),u,media;
      const cleanup=()=>{stop(timer);clear(watchdog);};
      const end=()=>{if(ended)return;ended=true;cleanup();hooks.end();};
      // Code-point offsets preserve surrogate pairs when revealing emoji.
      const offsets=[0];for(const char of Array.from(text))offsets.push(offsets.at(-1)+char.length);
      const animate=()=>{
        const count=Math.min(offsets.length-1,Math.floor((now()-startAt)/75));
        if(!audio || !boundaries)hooks.tick(offsets[Math.min(count,audio?Math.max(0,offsets.length-2):offsets.length-1)]);
        if(!audio && count===offsets.length-1)end();
      };
      const preview=()=>{
        if(ended)return;cleanup();audio=false;started=true;startAt=now();
        // Detach before cancel: some engines deliver the cancellation error synchronously.
        if(u){u.onstart=u.onboundary=u.onend=u.onerror=null;options.speech?.cancel();}
        hooks.start(false);hooks.error();timer=every(animate,50);
      };
      const fallbackSpeech=()=>{
        const voice=options.voiceFor(language);
        if(!voice || !options.speech || !options.Utterance){preview();return;}
        u=new options.Utterance(text);u.voice=voice;u.lang=voice.lang;
        u.onstart=()=>{if(ended||started)return;started=true;audio=true;startAt=now();hooks.start(true);timer=every(animate,50);};
        u.onboundary=e=>{if(ended)return;boundaries=true;hooks.boundary(Math.min(text.length,e.charIndex+(e.charLength||1)));};
        u.onend=end;u.onerror=preview;
        try{options.speech.speak(u);}catch(_){preview();}
      };
      if(mediaUrl){
        const AudioCtor=options.Audio||globalThis.Audio;
        if(AudioCtor){
          try{
            media=new AudioCtor(mediaUrl);media.preload="auto";
            media.onplay=()=>{if(ended||started)return;started=true;audio=true;startAt=now();hooks.start(true);timer=every(animate,50);};
            media.ontimeupdate=()=>{if(ended||!audio||!media.duration)return;hooks.tick(Math.min(text.length,Math.floor(media.currentTime/media.duration*text.length)));};
            media.onended=end;
            media.onerror=()=>{if(!ended){media.onplay=media.ontimeupdate=media.onended=media.onerror=null;cleanup();started=false;audio=false;fallbackSpeech();}};
            try{const result=media.play();if(result?.catch)result.catch(()=>media.onerror());}catch(_){media.onerror();}
          }catch(_){fallbackSpeech();}
        }else fallbackSpeech();
        return ()=>{ended=true;cleanup();if(media){media.onplay=media.ontimeupdate=media.onended=media.onerror=null;media.pause?.();media.src="";}if(u){u.onstart=u.onboundary=u.onend=u.onerror=null;options.speech?.cancel();}};
      }
      const voice=options.voiceFor(language);
      if(!voice || !options.speech || !options.Utterance){
        started=true;hooks.start(false);timer=every(animate,50);
      }else{
        u=new options.Utterance(text);u.voice=voice;u.lang=voice.lang;
        u.onstart=()=>{if(ended||started)return;started=true;audio=true;startAt=now();clear(watchdog);hooks.start(true);timer=every(animate,50);watchdog=later(preview,Math.max(30000,text.length*500));};
        u.onboundary=e=>{if(ended||!audio)return;boundaries=true;hooks.boundary(Math.min(text.length,e.charIndex+(e.charLength||1)));};
        u.onend=end;u.onerror=preview;
        watchdog=later(preview,5000);
        try{options.speech.speak(u);}catch(_){preview();}
      }
      return ()=>{ended=true;cleanup();if(u){u.onstart=u.onboundary=u.onend=u.onerror=null;options.speech?.cancel();}};
    };
  }
  function mount(canvas,{status,progress,onComplete,play,transcript}){
    const c=canvas.getContext("2d"), colors={neutral:"#8fe1d0",comfort:"#c6b5ee",happy:"#a9e6a0",urgent:"#f3c988"};
    const display=new Display({play,onComplete,onChange:m=>{
      if(status)status.textContent=labels[m.state];
      if(progress)progress.textContent=m.segment?"第 "+m.segment+" 段 · "+m.mode+" · "+Math.round(m.progress*100)+"%":"";
      if(transcript)transcript.textContent=(m.userText?"你："+m.userText+"\n":"")+(m.transcript?"機器人："+m.transcript:"");
      canvas.setAttribute("aria-label",labels[m.state]+"。"+m.caption);
    }});
    let raf;
    const reduced=window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    function line(x,y,x2,y2){c.beginPath();c.moveTo(x,y);c.lineTo(x2,y2);c.stroke();}
    function frame(time){
      const m=display.model,t=reduced?0:time/1000,color=colors[m.expression],off=m.state==="offline";
      c.fillStyle="#101f2b";c.fillRect(0,0,320,240);
      c.fillStyle="#a7bfcb";c.font="10px system-ui";c.textAlign="left";c.fillText("心語 / ESP SCREEN",12,18);
      c.textAlign="right";c.fillStyle=off?"#ffc3ac":"#8fe1d0";c.fillText(off?"× OFFLINE":"PC SIM · 320×240",308,18);
      c.save();c.translate(160,75+(m.state==="idle"?Math.sin(t*1.8)*3:0));
      c.strokeStyle=color;c.fillStyle=color;c.lineWidth=3;c.lineCap="round";
      c.globalAlpha=.12;c.beginPath();c.ellipse(0,0,64+Math.sin(t*1.8)*2,39,0,0,Math.PI*2);c.fill();c.globalAlpha=1;
      const blink=!reduced && t%4.6>4.42, eyeHeight=blink?1:12;
      if(off){line(-29,-10,-15,4);line(-29,4,-15,-10);line(15,-10,29,4);line(15,4,29,-10);}
      else if(m.expression==="happy"){
        for(const x of [-23,23]){c.beginPath();c.arc(x,0,9,Math.PI,Math.PI*2);c.stroke();}
      }else if(m.expression==="comfort"){
        line(-32,-3,-16,0);line(16,0,32,-3);
      }else{
        c.beginPath();c.ellipse(-23,-4,5,eyeHeight,0,0,Math.PI*2);c.ellipse(23,-4,5,eyeHeight,0,0,Math.PI*2);c.fill();
      }
      c.beginPath();
      if(m.state==="speaking"){c.ellipse(0,18,10,4+Math.abs(Math.sin(t*9))*5,0,0,Math.PI*2);c.stroke();}
      else{c.arc(0,10,14,.2,Math.PI-.2);c.stroke();}
      if(m.expression==="urgent"){c.font="bold 12px system-ui";c.textAlign="center";c.fillText("! 需要關注",0,48);}
      if(m.state==="thinking" || m.state==="transcribing"){
        for(let i=0;i<3;i++){c.globalAlpha=reduced?.7:.25+.75*(Math.sin(t*4-i)+1)/2;c.beginPath();c.arc(66+i*9,-12,2.5,0,Math.PI*2);c.fill();}c.globalAlpha=1;
      }
      c.restore();
      if(m.state==="listening"){
        c.strokeStyle="#9cd8f3";c.lineWidth=2;c.strokeRect(15,106,5,10);line(12,114,12,119);line(12,119,23,119);line(18,119,18,123);
        for(let i=0;i<36;i++){
          const height=2+m.level*18*(.35+.65*Math.abs(Math.sin(i*1.4+t*8)));
          c.fillStyle="#9cd8f3";c.fillRect(33+i*7,116-height/2,3,height);
        }
      }
      c.fillStyle="#eef5f6";c.font="12px system-ui";c.textAlign="left";
      c.fillText(labels[m.state]||m.state,12,142);
      if(m.expression==="comfort" && m.state!=="speaking"){c.textAlign="right";c.fillStyle=color;c.fillText("慢慢來，我在",308,142);}
      c.fillStyle="#203440";c.fillRect(8,152,304,70);
      c.fillStyle="#b8d8d5";c.font="10px system-ui";c.textAlign="left";
      c.fillText("聲紋使用者：" + m.user,16,166);
      c.font="14px system-ui";c.fillStyle="#f3f7f9";c.textAlign="left";
      const lines=wrapText(m.caption,text=>c.measureText(text).width,280);
      // Rolling viewport: all long STT lines cycle; speech follows revealed text.
      const max=Math.max(0,lines.length-3);
      const start=m.state==="speaking"||m.state==="idle"?max:Math.floor(Math.max(0,Date.now()-m.changedAt)/2200)%(max+1);
      lines.slice(start,start+2).forEach((text,i)=>c.fillText(text,16,184+i*20));
      c.fillStyle="#a7bfcb";c.font="9px system-ui";
      c.fillText(lines.length>3?"行 "+(start+1)+"–"+Math.min(start+3,lines.length)+" / "+lines.length:"",12,235);
      c.textAlign="right";c.fillText(m.segment?"SEG "+m.segment+" · "+Math.round(m.progress*100)+"%":"2.4″ · 邏輯預覽",308,235);
      c.fillStyle=color;c.fillRect(8,221,304*m.progress,2);
      raf=requestAnimationFrame(frame);
    }
    raf=requestAnimationFrame(frame);
    display.dispose=()=>{cancelAnimationFrame(raf);display.reset();};
    return display;
  }
  return {Display,wrapText,mount,createSpeechPlayer};
});
