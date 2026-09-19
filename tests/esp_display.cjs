// These regressions catch premature idle, lost long captions and stale playback callbacks.
const assert = require("node:assert/strict"), fs = require("node:fs");
const file = "services/dashboard/esp-display.js";
assert.ok(fs.existsSync(file), "ESP display controller must exist");
const {Display, wrapText} = require("../" + file);
let now=0, jobs=[], played=[], cancellations=0, finished=0;
const display=new Display({
  now:()=>now, later:(fn,ms)=>{const job={fn,at:now+ms};jobs.push(job);return job;},
  clear:job=>{jobs=jobs.filter(x=>x!==job);},
  play:(text,lang,hooks)=>{played.push({text,lang,hooks});return ()=>cancellations++;},
  onComplete:()=>finished++
});
function advance(ms){now+=ms;const due=jobs.filter(x=>x.at<=now);jobs=jobs.filter(x=>x.at>now);due.forEach(x=>x.fn());}
display.begin();display.setState("listening");display.setLevel(.7);
assert.equal(display.model.level,.7);
display.setState("transcribing");
assert.equal(display.model.caption,"正在理解…");
display.event({type:"turn.started",turn_id:"a"});
display.event({type:"stt.final",turn_id:"a",payload:{text:"我今天心情不好"}});
display.event({type:"display.state",turn_id:"a",payload:{state:"thinking"}});
assert.equal(display.model.caption,"我今天心情不好","thinking must retain the user's STT");
display.event({type:"tts.segment",turn_id:"a",payload:{segment_id:"a-0",text:"我會陪伴你。",result:{emotion:"negative",risk:{risk_level:"urgent"}}}});
assert.equal(display.model.expression,"urgent","risk takes precedence over emotion");
assert.equal(display.model.state,"speaking");
played[0].hooks.start(true);played[0].hooks.boundary(2);
assert.equal(display.model.caption,"我會");
assert.equal(display.model.progress,2/6);
display.event({type:"tts.segment",turn_id:"a",payload:{segment_id:"a-1",text:"慢慢說。"}});
display.event({type:"tts.segment",turn_id:"a",payload:{segment_id:"a-1",text:"慢慢說。"}});
display.event({type:"tts.end",turn_id:"a",payload:{}});
display.event({type:"display.state",turn_id:"a",payload:{state:"idle"}});
assert.equal(played.length,1,"segments must play serially; duplicates must not replay");
assert.equal(finished,0,"network end is not playback end");
played[0].hooks.end();
assert.equal(played.length,2);
played[0].hooks.end();
assert.equal(finished,0,"duplicate end callback must not finish the next segment");
played[1].hooks.start(false);
assert.equal(display.model.mode,"字幕預覽（無音訊）");
played[1].hooks.end();
assert.equal(display.model.caption,"慢慢說。");
assert.equal(display.model.transcript,"我會陪伴你。慢慢說。");
assert.equal(finished,1);
assert.equal(display.model.state,"idle");
advance(4999);assert.equal(display.model.caption,"慢慢說。");
advance(1);assert.equal(display.model.caption,"");
const text="長字幕ABC🙂".repeat(60);
const lines=wrapText(text,s=>Array.from(s).length*10,280);
assert.equal(lines.join(""),text,"no characters may be discarded");
assert.ok(lines.every(s=>Array.from(s).length<=28));
display.begin();display.event({type:"turn.started",turn_id:"b"});
display.event({type:"tts.segment",turn_id:"a",payload:{text:"old"}});
assert.equal(played.length,2,"old turn must be ignored");
display.event({type:"tts.segment",turn_id:"b",payload:{text:"new"}});
const stale=played[2].hooks;
display.offline("網絡中斷");
stale.end();stale.boundary(1);advance(10000);
assert.equal(display.model.state,"offline");
assert.equal(display.model.caption,"網絡中斷");
assert.ok(cancellations>0);
display.begin();display.event({type:"turn.started",turn_id:"c"});
display.event({type:"tts.segment",turn_id:"c",payload:{text:"happy",result:{emotion:"positive",risk:{risk_level:"none"}}}});
assert.equal(display.model.expression,"happy");
display.event({type:"turn.failed",turn_id:"c",payload:{error:"重試"}});
assert.equal(display.model.state,"error");
console.log("PASS: speech lifecycle, segment queue, risk, long text, caption hold, offline and stale events");
display.begin("request-new");
display.event({type:"turn.started",turn_id:"late",payload:{request_id:"request-old"}});
assert.equal(display.turn,null,"late previous request must not adopt the new local turn");
