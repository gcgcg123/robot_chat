// Browser-independent controller regression: exercise the real simulator.js event handlers.
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
class Element {
  constructor(id) {this.id=id;this.value="";this.hidden=false;this.disabled=false;this.style={};this.textContent="";}
  pause() {}
  removeAttribute(name) {delete this[name];}
  replaceChildren() {}
  add() {}
  getContext() {return {fillRect(){},fillText(){}};}
}
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
const user={user_id:"u",display_name:"Demo",preferred_language:"en-US"};
const requests=[], stored=new Map();
let failNextSample=false;
const speech={cancel(){},getVoices(){return [];},addEventListener(){}};
let processor;
class Context {
  sampleRate=16000;
  async resume(){}
  async close(){}
  createMediaStreamSource(){return {connect(){},disconnect(){}};}
  createScriptProcessor(){processor={disconnect(){},connect(){}};return processor;}
  createGain(){return {gain:{},disconnect(){},connect(){processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(64000).fill(.1)}});}};}
}
class Socket {
  static OPEN=1;
  readyState=1;
  constructor(){setImmediate(()=>this.onopen?.());}
  close(){}
}
const sandbox={
  document:{getElementById:el}, window:{AudioContext:Context,speechSynthesis:speech,addEventListener(){}},
  AudioContext:Context, WebSocket:Socket, speechSynthesis:speech,
  navigator:{mediaDevices:{async getUserMedia(){return {getTracks:()=>[{stop(){}}]};}}},
  location:{protocol:"http:",host:"localhost",search:"?user=u"}, console,
  URL:{createObjectURL:()=> "blob:preview",revokeObjectURL(){}}, URLSearchParams, Option:class{},
  Blob, FormData, ArrayBuffer, DataView, Float32Array,
  setTimeout:(f,ms)=>{if(ms===20000)setImmediate(f);return 1;},clearTimeout(){},setInterval:()=>1,clearInterval(){},
  async fetch(path,options={}) {
    requests.push({path,options});
    let data={},status=200;
    if(path==="/api/auth/session") data={csrf_token:"test"};
    else if(path==="/api/enrollment-languages")data={"en-US":{label:"English",tts:"en-US",asr:"en"}};
    else if(path.startsWith("/api/users?"))data={items:[user],total:1};
    else if(path==="/api/simulator/sessions")data={session_id:"s",device_id:"d"};
    else if(path==="/api/enrollments")data={enrollment_id:"e",expires_at:Date.now()/1000+600,language:"en-US",prompts:["First passage","Second passage","Third passage"]};
    else if(path.endsWith("/samples")) {
      if(failNextSample){failNextSample=false;status=503;data={detail:"try_again"};}
      else {stored.set(options.body.get("step"),options.body.get("file"));data={sample_count:stored.size};}
    } else if(path.endsWith("/complete")) data={state:"completed"};
    return {ok:status===200,status,json:async()=>data};
  }
};
vm.runInNewContext(fs.readFileSync("services/dashboard/simulator.js","utf8"),sandbox);
(async()=>{
  await new Promise(setImmediate);await new Promise(setImmediate);
  assert.equal(el("user-select").value,"u");
  await el("enroll-start").onclick();
  assert.equal(el("reading-prompt").textContent,"First passage");
  assert.equal(el("enroll-confirm").disabled,true);
  await el("enroll-record").onclick();
  assert.equal(stored.size,0,"recording must not auto-submit");
  assert.equal(el("enroll-confirm").disabled,false);
  await el("enroll-record").onclick();
  assert.equal(stored.size,0,"rerecord must not advance");
  failNextSample=true;
  await el("enroll-confirm").onclick();
  assert.equal(el("sample-preview").hidden,false,"failed upload must retain preview");
  assert.equal(el("sample-count").textContent,"0 / 3");
  await el("enroll-confirm").onclick();
  assert.equal(el("sample-count").textContent,"1 / 3");
  assert.equal(el("reading-prompt").textContent,"Second passage");
  assert.match(el("toast").textContent,/第 1 段已保存/);
  assert.equal(el("enroll-confirm").disabled,true);
  for(let step=2;step<=3;step++){
    await el("enroll-record").onclick();
    await el("enroll-confirm").onclick();
    assert.equal(el("sample-count").textContent,step+" / 3");
  }
  assert.equal(el("wizard").hidden,true);
  assert.match(el("toast").textContent,/聲紋登記完成/);
  assert.equal(requests.filter(r=>r.path.endsWith("/complete")).length,1);
  assert.equal(el("conversation-record").disabled,false);
  console.log("PASS: preview, rerecord, failed upload retry, three confirmations and final completion");
})().catch(e=>{console.error(e);process.exitCode=1;});

