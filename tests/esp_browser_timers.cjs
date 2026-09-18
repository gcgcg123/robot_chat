// Browser timer receiver regression: Node timers do not enforce Window branding.
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const context=vm.createContext({assert});
vm.runInContext(`
  globalThis.window=globalThis;
  let pending, cleared=0;
  globalThis.setTimeout=function(fn,ms){
    "use strict";
    if(this!==globalThis)throw new TypeError("Illegal invocation: setTimeout");
    pending=fn;return 1;
  };
  globalThis.clearTimeout=function(id){
    "use strict";
    if(this!==globalThis)throw new TypeError("Illegal invocation: clearTimeout");
    cleared++;
  };
`,context);
vm.runInContext(fs.readFileSync("services/dashboard/esp-display.js","utf8"),context);
assert.doesNotThrow(()=>vm.runInContext(`
  const display=new EspDisplay.Display();
  display.begin();
  display.model.caption="最後一句";
  display.done=true;display.finish();
  assert.equal(display.model.caption,"最後一句");
  pending();
  assert.equal(display.model.caption,"");
  display.offline();
  assert.equal(display.model.state,"offline");
  assert.ok(cleared>=3);
`,context),"initialization, subtitle hold and reset must use the browser timer receiver");
console.log("PASS: browser timer receivers during initialization, caption hold and reset");

