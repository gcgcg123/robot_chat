const assert=require("node:assert/strict");
const {createSpeechPlayer}=require("../services/dashboard/esp-display.js");
assert.equal(typeof createSpeechPlayer,"function","speech adapter must provide a cancellable player");
let now=0,interval,timeout,utterance,ended=0,mode,ticks=[],cancelled=0;
const env={now:()=>now,setInterval:f=>(interval=f,1),clearInterval:()=>{interval=null;},
setTimeout:f=>(timeout=f,2),clearTimeout:()=>{timeout=null;},
speech:{speak:u=>{utterance=u;},cancel:()=>cancelled++},Utterance:class{constructor(text){this.text=text;}}};
const hooks={start:a=>mode=a,tick:n=>ticks.push(n),boundary:n=>ticks.push(n),end:()=>ended++,error(){}};
const player=createSpeechPlayer({...env,voiceFor:()=>({lang:"en-US"})});
const cancel=player("hello there","en-US",hooks);
assert.equal(ended,0);
utterance.onstart();now=200;interval();assert.equal(mode,true);
utterance.onboundary({charIndex:6,charLength:5});assert.equal(ticks.at(-1),11);
now=10000;interval();assert.equal(ended,0,"estimated timing must not finish real audio");
utterance.onend();assert.equal(ended,1);
const staleEnd=utterance.onend;cancel();staleEnd();assert.equal(ended,1,"cancel must ignore old browser callbacks");
const silent=createSpeechPlayer({...env,voiceFor:()=>null});
silent("字幕🙂","yue-HK",hooks);assert.equal(mode,false);
now+=2000;interval();assert.equal(ended,2,"no voice must complete as an explicit caption preview");
player("start timeout","en-US",hooks);timeout();assert.equal(mode,false);
assert.ok(cancelled>0);now+=5000;interval();assert.equal(ended,3);
console.log("PASS: speech boundaries, actual end, cancellation, no voice and start timeout");
