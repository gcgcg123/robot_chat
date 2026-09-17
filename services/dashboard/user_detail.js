(async function () {
  const id=decodeURIComponent(location.pathname.split('/').pop()), path='/api/users/'+encodeURIComponent(id);
  const $=x=>document.getElementById(x), labels={'yue-HK':'粵語','zh-CN':'普通話','en-US':'English'};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let user, csrf, busy=false, memoryConsentGranted=false;
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
  try {
    const auth=await api('/api/auth/session');csrf=auth.csrf_token;
    const [u,s,risk,rows,memories,consent]=await Promise.all([api(path),api(path+'/summary?period=week'),api(path+'/risk-events'),api('/api/conversations'),api(path+'/memories'),api(path+'/memory-consent')]);
    user=u;render();
    $('count').textContent=s.conversation_count;
    $('mood').textContent=Object.entries(s.emotion_distribution).sort((a,b)=>b[1]-a[1])[0]?.[0]||'—';
    $('risk').textContent=s.risk.risk_level;
    $('conversations').innerHTML=rows.filter(x=>x.user_id===id).slice(0,30).map(x=>'<p><b>'+new Date(x.created_at*1000).toLocaleString()+'</b> · '+esc(x.emotion)+'<br>'+esc(x.input_text)+' → '+esc(x.response_text)+'</p>').join('')||'尚無對話紀錄';
    $('risks').innerHTML=risk.map(x=>'<p><b>'+esc(x.risk_level)+'</b> · '+esc(x.review_status)+'<br>'+esc((x.evidence||[]).join('、'))+'</p>').join('')||'目前沒有風險事件';
    renderMemories(memories, consent.granted);
  } catch(e) {$('name').textContent=e.message;$('status').textContent='無法載入';return;}
  function renderMemories(items, granted) {
    memoryConsentGranted=Boolean(granted);
    $('memory-consent').textContent=granted?'当前已授权：私人记忆可在声纹确认后参与对话。':'当前未授权：私人记忆不会注入对话。共享记忆仍可使用。';
    $('grant-memory').disabled=granted;$('revoke-memory').disabled=!granted;
    $('memories').innerHTML=items.map(x=>'<p><b>'+esc(x.approved?'已批准':'待批准')+'</b> · '+esc(x.text)+' <button data-memory="'+esc(x.chunk_id)+'" class="refresh-btn">删除</button></p>').join('')||'暂无个人记忆';
    for(const button of document.querySelectorAll('[data-memory]')) button.onclick=async()=>{if(busy)return;lock(true);try{await api(path+'/memories/'+encodeURIComponent(button.dataset.memory),{method:'DELETE'});const current=await api(path+'/memories');const consent=await api(path+'/memory-consent');renderMemories(current,consent.granted);}catch(e){ $('feedback').textContent=e.message;}finally{lock(false);}};
  }
  async function setMemoryConsent(granted) {
    if(busy)return;lock(true);
    try{const result=await api(path+'/memory-consent',{method:'POST',body:JSON.stringify({granted})});const current=await api(path+'/memories');renderMemories(current,result.granted);$('feedback').textContent=granted?'已允许私人记忆参与对话。':'已撤回私人记忆授权。';}catch(e){$('feedback').textContent=e.message;}finally{lock(false);}
  }
  $('grant-memory').type='button';
  $('revoke-memory').type='button';
  $('grant-memory').onclick=()=>setMemoryConsent(true);
  $('revoke-memory').onclick=()=>setMemoryConsent(false);
  $('memory-form').onsubmit=async e=>{e.preventDefault();if(busy)return;lock(true);try{const body=Object.fromEntries(new FormData(e.target));body.approved=Boolean(body.approved);await api(path+'/memories',{method:'POST',body:JSON.stringify(body)});e.target.reset();const current=await api(path+'/memories');const consent=await api(path+'/memory-consent');renderMemories(current,consent.granted);$('feedback').textContent='记忆已保存。';}catch(e){$('feedback').textContent=e.message;}finally{lock(false);}};
  function lock(value) {
    busy=value;
    for(const button of document.querySelectorAll('button'))button.disabled=value;
    if(!value) {
      $('grant-memory').disabled=memoryConsentGranted;
      $('revoke-memory').disabled=!memoryConsentGranted;
    }
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
