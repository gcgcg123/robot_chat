(async function () {
  const id=decodeURIComponent(location.pathname.split('/').pop()), path='/api/users/'+encodeURIComponent(id);
  const $=x=>document.getElementById(x), labels={'yue-HK':'粵語','zh-CN':'普通話','en-US':'English'};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let user, csrf, busy=false, memoryData=null, memoryBusy=false;
  const tierNames={slot:'槽位',hot:'熱記憶',cold:'冷記憶'};
  const tierHints={slot:'最常被用到、最穩定的事實',hot:'最近記得、還沒進槽位的事',cold:'偽刪除：仍可被檢索到，被重新提起就會復活'};
  async function api(p,options={}) {
    const r=await fetch(p,{cache:'no-store',...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf||'',...options.headers}});
    const data=await r.json().catch(()=>({}));
    if(!r.ok)throw Error(r.status===401?'登入已過期，請返回 Dashboard 登入。':r.status===404?'使用者已不存在。':typeof data.detail==='string'?data.detail:'請檢查輸入內容。');
    return data;
  }
  function render() {
    $('name').textContent=user.display_name||user.user_id;
    $('profile').textContent=(user.gender||'不透露')+' · '+(user.age_at_registration==null?'年齡未提供':user.age_at_registration+' 歲')+' · 登記於 '+new Date(user.created_at*1000).toLocaleDateString();
    $('languages').textContent='偏好對話語言：'+labels[user.preferred_language]+'　｜　聲紋登記語言：'+(labels[user.enrollment_language]||'尚未登記／舊版未記錄');
    $('status').textContent=user.status==='active'?'啟用':'停用';$('status').className='status-badge '+(user.status==='active'?'ok':'error');
    $('toggle-user').textContent=user.status==='active'?'停用使用者':'啟用使用者';
    $('reenroll').href='/simulator?user='+encodeURIComponent(id);
    $('reenroll').hidden=user.status!=='active';
  }
  function renderMemories() {
    if(!memoryData)return;
    const t=memoryData.thresholds, c=memoryData.counts;
    $('memory-tiers').innerHTML=[
      '<span class="memory-chip">槽位 '+c.slot+' / '+t.slot_capacity+'</span>',
      '<span class="memory-chip">熱記憶 '+c.hot+'</span>',
      '<span class="memory-chip">冷記憶 '+c.cold+'</span>',
      '<span class="memory-chip muted">升槽位 ≥'+t.promote_score+'　降冷 <'+t.demote_score+'　半衰期 '+t.half_life_days+' 天</span>',
      memoryData.embedding_available?'':'<span class="memory-chip warn">嵌入模型未載入：檢索退化為字面比對</span>'
    ].join('');
    if(!memoryData.items.length){$('memories').innerHTML='這位使用者還沒有任何記憶。';return;}
    $('memories').innerHTML=memoryData.items.map(m=>{
      const soft=m.tier==='cold'?' muted':'';
      return '<article class="memory-row'+soft+'" data-id="'+esc(m.chunk_id)+'">'
        +'<div class="memory-head"><span class="tier-badge t-'+esc(m.tier)+'" title="'+esc(tierHints[m.tier]||'')+'">'+esc(tierNames[m.tier]||m.tier)+'</span>'
        +'<span class="muted">分數 '+m.score+'　命中 '+m.hits+'　重要度 '+m.importance
        +(m.slot_key?'　鍵 '+esc(m.slot_key):'')+(m.approved?'　已確認':'　<span class="warn">待確認</span>')+'</span></div>'
        +'<p class="memory-text">'+esc(m.text)+'</p>'
        +(m.probe?'<p class="muted">檢索句：'+esc(m.probe)+'</p>':'')
        +'<div class="memory-actions">'
        +(m.tier==='slot'?'':'<button data-memory-tier="slot">升到槽位</button>')
        +(m.tier==='hot'?'':'<button data-memory-tier="hot">放到熱記憶</button>')
        +(m.tier==='cold'?'':'<button data-memory-tier="cold">降為冷記憶</button>')
        +'<button data-memory-reset="1">歸零衰減</button>'
        +'<button data-memory-delete="1" class="danger">刪除</button></div></article>';
    }).join('');
    for(const el of $('memories').querySelectorAll('[data-memory-tier]'))el.onclick=()=>patchMemory(el.closest('.memory-row').dataset.id,{tier:el.dataset.memoryTier});
    for(const el of $('memories').querySelectorAll('[data-memory-reset]'))el.onclick=()=>patchMemory(el.closest('.memory-row').dataset.id,{reset_decay:true});
    for(const el of $('memories').querySelectorAll('[data-memory-delete]'))el.onclick=()=>deleteMemory(el.closest('.memory-row').dataset.id);
  }
  async function loadMemories() {
    try{memoryData=await api(path+'/memories?include_inactive=true');renderMemories();$('memory-feedback').textContent='';}
    catch(e){$('memories').innerHTML='無法載入記憶：'+esc(e.message);}
  }
  async function patchMemory(memoryId,body) {
    if(memoryBusy)return;memoryBusy=true;$('memory-feedback').textContent='處理中…';
    try{await api(path+'/memories/'+encodeURIComponent(memoryId),{method:'PATCH',body:JSON.stringify(body)});await loadMemories();$('memory-feedback').textContent='記憶已更新。';}
    catch(e){$('memory-feedback').textContent=e.message;}
    finally{memoryBusy=false;}
  }
  async function deleteMemory(memoryId) {
    if(memoryBusy)return;
    if(!confirm('確定要永久刪除這則記憶？冷記憶也可以改用「降為冷記憶」保留檢索能力。'))return;
    memoryBusy=true;
    try{await api(path+'/memories/'+encodeURIComponent(memoryId),{method:'DELETE'});await loadMemories();$('memory-feedback').textContent='記憶已刪除。';}
    catch(e){$('memory-feedback').textContent=e.message;}
    finally{memoryBusy=false;}
  }
  try {
    const auth=await api('/api/auth/session');csrf=auth.csrf_token;
    const [u,s,risk,rows]=await Promise.all([api(path),api(path+'/summary?period=week'),api(path+'/risk-events'),api('/api/conversations')]);
    user=u;render();
    $('count').textContent=s.conversation_count;
    $('mood').textContent=Object.entries(s.emotion_distribution).sort((a,b)=>b[1]-a[1])[0]?.[0]||'—';
    $('risk').textContent=s.risk.risk_level;
    $('conversations').innerHTML=rows.filter(x=>x.user_id===id).slice(0,30).map(x=>'<p><b>'+new Date(x.created_at*1000).toLocaleString()+'</b> · '+esc(x.emotion)+'<br>'+esc(x.input_text)+' → '+esc(x.response_text)+'</p>').join('')||'尚無對話紀錄';
    $('risks').innerHTML=risk.map(x=>'<p><b>'+esc(x.risk_level)+'</b> · '+esc(x.review_status)+'<br>'+esc((x.evidence||[]).join('、'))+'</p>').join('')||'目前沒有風險事件';
    await loadMemories();
  } catch(e) {$('name').textContent=e.message;$('status').textContent='無法載入';return;}
  function lock(value) {
    busy=value;
    for(const button of document.querySelectorAll('button'))button.disabled=value;
  }
  $('edit-profile').onclick=()=>{
    for(const key of ['display_name','gender','age_at_registration','preferred_language'])$('edit-form').elements[key].value=user[key]??'';
    $('edit-error').textContent='';$('edit-dialog').showModal();
  };
  $('edit-cancel').onclick=()=>{$('edit-dialog').close();};
  $('edit-form').onsubmit=async e=>{
    e.preventDefault();if(busy)return;lock(true);
    try {
      const body=Object.fromEntries(new FormData(e.target));
      body.age_at_registration=body.age_at_registration===''?null:Number(body.age_at_registration);
      user=await api(path,{method:'PATCH',body:JSON.stringify(body)});render();$('edit-dialog').close();$('feedback').textContent='個人資料已保存；對話語言會於下一輪套用。';
    }catch(e){$('edit-error').textContent=e.message;}finally{lock(false);}
  };
  $('memory-refresh').onclick=loadMemories;
  $('memory-form').onsubmit=async e=>{
    e.preventDefault();
    const form=e.target, body=Object.fromEntries(new FormData(form));
    body.importance=Number(body.importance||0.6);
    $('memory-feedback').textContent='儲存中…';
    try {
      await api(path+'/memories',{method:'POST',body:JSON.stringify(body)});
      form.reset();$('memory-form').elements.importance.value='0.6';
      await loadMemories();$('memory-feedback').textContent='已新增；下一輪對話就有機會被檢索到。';
    } catch(err){$('memory-feedback').textContent=err.message;}
  };
  $('toggle-user').onclick=async()=>{
    if(busy)return;lock(true);
    try{user=await api(path,{method:'PATCH',body:JSON.stringify({status:user.status==='active'?'disabled':'active'})});render();$('feedback').textContent='使用者狀態已更新。';}
    catch(e){$('feedback').textContent=e.message;}finally{lock(false);}
  };
  $('delete-user').onclick=()=>{
    $('delete-description').textContent='將刪除「'+user.display_name+'」；使用者 ID：'+id;
    $('delete-confirmation').value='';$('delete-error').textContent='';$('delete-dialog').showModal();
  };
  $('delete-cancel').onclick=()=>{$('delete-dialog').close();};
  $('delete-form').onsubmit=async e=>{
    e.preventDefault();if(busy)return;
    if($('delete-confirmation').value!==id){$('delete-error').textContent='ID 不一致，未執行刪除。';return;}
    lock(true);
    try{await api(path,{method:'DELETE',body:JSON.stringify({confirm_user_id:id})});sessionStorage.setItem('iot-user-notice','使用者及相關本機資料已刪除。');location.href='/dashboard#users';}
    catch(e){$('delete-error').textContent=e.message;lock(false);}
  };
  for(const d of document.querySelectorAll('dialog'))d.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
})();
