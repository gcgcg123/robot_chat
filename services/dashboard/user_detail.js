(async function () {
  const id=decodeURIComponent(location.pathname.split('/').pop()), path='/api/users/'+encodeURIComponent(id);
  const $=x=>document.getElementById(x), labels={'yue-HK':'粤语','zh-CN':'普通话','en-US':'English'};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let user, csrf, busy=false;
  async function api(p,options={}) {
    const r=await fetch(p,{cache:'no-store',...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf||'',...options.headers}});
    const data=await r.json().catch(()=>({}));
    if(!r.ok)throw Error(r.status===401?'登录已过期，请返回 Dashboard 登录。':r.status===404?'用户已不存在。':typeof data.detail==='string'?data.detail:'请检查输入内容。');
    return data;
  }
  function render() {
    $('name').textContent=user.display_name||user.user_id;
    $('profile').textContent=(user.gender||'不透露')+' · '+(user.age_at_registration==null?'年龄未提供':user.age_at_registration+' 岁')+' · 登记于 '+new Date(user.created_at*1000).toLocaleDateString();
    $('languages').textContent='偏好对话语言：'+labels[user.preferred_language]+'　｜　声纹登记语言：'+(labels[user.enrollment_language]||'尚未登记／旧版未记录');
    $('status').textContent=user.status==='active'?'启用':'停用';$('status').className='status-badge '+(user.status==='active'?'ok':'error');
    $('toggle-user').textContent=user.status==='active'?'停用用户':'启用用户';
    $('reenroll').href='/simulator?user='+encodeURIComponent(id);
    $('reenroll').hidden=user.status!=='active';
  }
  try {
    const auth=await api('/api/auth/session');csrf=auth.csrf_token;
    const [u,s,risk,rows]=await Promise.all([api(path),api(path+'/summary?period=week'),api(path+'/risk-events'),api('/api/conversations')]);
    user=u;render();
    $('count').textContent=s.conversation_count;
    $('mood').textContent=Object.entries(s.emotion_distribution).sort((a,b)=>b[1]-a[1])[0]?.[0]||'—';
    $('risk').textContent=s.risk.risk_level;
    $('conversations').innerHTML=rows.filter(x=>x.user_id===id).slice(0,30).map(x=>'<p><b>'+new Date(x.created_at*1000).toLocaleString()+'</b> · '+esc(x.emotion)+'<br>'+esc(x.input_text)+' → '+esc(x.response_text)+'</p>').join('')||'尚无对话记录';
    $('risks').innerHTML=risk.map(x=>'<p><b>'+esc(x.risk_level)+'</b> · '+esc(x.review_status)+'<br>'+esc((x.evidence||[]).join('、'))+'</p>').join('')||'目前没有风险事件';
  } catch(e) {$('name').textContent=e.message;$('status').textContent='无法载入';return;}
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
      user=await api(path,{method:'PATCH',body:JSON.stringify(body)});render();$('edit-dialog').close();$('feedback').textContent='个人资料已保存；对话语言会于下一轮套用。';
    }catch(e){$('edit-error').textContent=e.message;}finally{lock(false);}
  };
  $('toggle-user').onclick=async()=>{
    if(busy)return;lock(true);
    try{user=await api(path,{method:'PATCH',body:JSON.stringify({status:user.status==='active'?'disabled':'active'})});render();$('feedback').textContent='用户状态已更新。';}
    catch(e){$('feedback').textContent=e.message;}finally{lock(false);}
  };
  $('delete-user').onclick=()=>{
    $('delete-description').textContent='将删除「'+user.display_name+'」；用户 ID：'+id;
    $('delete-confirmation').value='';$('delete-error').textContent='';$('delete-dialog').showModal();
  };
  $('delete-cancel').onclick=()=>{$('delete-dialog').close();};
  $('delete-form').onsubmit=async e=>{
    e.preventDefault();if(busy)return;
    if($('delete-confirmation').value!==id){$('delete-error').textContent='ID 不一致，未执行删除。';return;}
    lock(true);
    try{await api(path,{method:'DELETE',body:JSON.stringify({confirm_user_id:id})});sessionStorage.setItem('iot-user-notice','用户及相关本机资料已删除。');location.href='/dashboard#users';}
    catch(e){$('delete-error').textContent=e.message;lock(false);}
  };
  for(const d of document.querySelectorAll('dialog'))d.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
})();
