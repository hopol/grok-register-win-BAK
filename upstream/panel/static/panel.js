const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
let lastLogLen = 0;

function renderIcons(){ if(window.lucide){ window.lucide.createIcons(); } }
function toast(message){
  const el=document.getElementById('toast');
  el.textContent=message; el.style.display='block';
  clearTimeout(window.__toastTimer); window.__toastTimer=setTimeout(()=>el.style.display='none',2400);
}
async function api(url,opt={}){
  const headers=Object.assign({},opt.headers||{});
  if((opt.method||'GET').toUpperCase()!=='GET') headers['X-CSRF-Token']=csrfToken;
  const response=await fetch(url,Object.assign({credentials:'same-origin'},opt,{headers}));
  const data=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(data.error||response.statusText||'request failed');
  return data;
}
function toggleSidebar(open){
  document.getElementById('sidebar')?.classList.toggle('open',!!open);
  document.getElementById('mobile_backdrop')?.classList.toggle('show',!!open);
}
function onEmailProviderChange(){
  const provider=document.getElementById('email_provider').value;
  document.getElementById('email_builtin_extra').classList.toggle('hidden',provider!=='tempmailer');
  document.getElementById('email_local_worker_box').classList.toggle('hidden',provider!=='local_worker');
  document.getElementById('email_custom_box').classList.toggle('hidden',provider!=='custom');
}
async function loadEmailConfig(){
  const hint=document.getElementById('email_hint');
  try{
    const result=await api('/api/config/email'); const e=result.email||{};
    document.getElementById('email_provider').value=e.provider||'tempmailer';
    document.getElementById('email_failover').checked=!!e.email_failover;
    document.getElementById('tempmailer_domain').value=e.tempmailer_domain||'';
    document.getElementById('local_worker_api_base').value=e.local_worker_api_base||'';
    document.getElementById('local_worker_domain').value=e.local_worker_domain||'';
    document.getElementById('local_worker_admin_token').placeholder=e.local_worker_token_set?'已安全保存，留空保持不变':'请输入管理 Token';
    document.getElementById('custom_api_base').value=e.custom_api_base||'';
    document.getElementById('custom_api_key').placeholder=e.custom_api_key_set?'已安全保存，留空保持不变':'可选';
    document.getElementById('custom_auth_mode').value=e.custom_auth_mode||'x-admin-auth';
    document.getElementById('custom_domain').value=e.custom_domain||'';
    document.getElementById('custom_path_accounts').value=e.custom_path_accounts||'/admin/new_address';
    document.getElementById('custom_path_messages').value=e.custom_path_messages||'/api/mails';
    document.getElementById('custom_path_token').value=e.custom_path_token||'/api/token';
    hint.textContent=e.hint||'';
    const state=document.getElementById('email_state'); state.textContent=e.provider==='local_worker'?'本地域名':(e.provider||'已加载'); state.className='pill';
    onEmailProviderChange();
  }catch(error){ hint.textContent='加载失败: '+error.message; document.getElementById('email_state').textContent='异常'; }
}
async function saveEmailConfig(){
  const body={
    provider:document.getElementById('email_provider').value,
    email_failover:document.getElementById('email_failover').checked,
    tempmailer_domain:document.getElementById('tempmailer_domain').value.trim(),
    local_worker_api_base:document.getElementById('local_worker_api_base').value.trim(),
    local_worker_domain:document.getElementById('local_worker_domain').value.trim(),
    local_worker_admin_token:document.getElementById('local_worker_admin_token').value,
    custom_api_base:document.getElementById('custom_api_base').value.trim(),
    custom_api_key:document.getElementById('custom_api_key').value,
    custom_auth_mode:document.getElementById('custom_auth_mode').value,
    custom_domain:document.getElementById('custom_domain').value.trim(),
    custom_path_accounts:document.getElementById('custom_path_accounts').value.trim(),
    custom_path_messages:document.getElementById('custom_path_messages').value.trim(),
    custom_path_token:document.getElementById('custom_path_token').value.trim()
  };
  const result=await api('/api/config/email',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  document.getElementById('local_worker_admin_token').value=''; document.getElementById('custom_api_key').value='';
  const state=document.getElementById('email_state'); state.textContent=result.email?.provider==='local_worker'?'本地域名':(result.email?.provider||'已保存');
  toast(result.message||'邮箱设置已保存'); return result;
}
function toggleSelectAllFiles(on){ document.querySelectorAll('.chk-file').forEach(box=>box.checked=!!on); const all=document.getElementById('chk_all_files'); if(all) all.checked=!!on; }
async function deleteSelectedFiles(){
  const files=[...document.querySelectorAll('.chk-file:checked')].map(box=>box.value);
  if(!files.length){toast('请先选择账号文件');return}
  if(!confirm('确认删除选中的 '+files.length+' 个账号文件？')) return;
  const result=await api('/api/accounts/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files})});
  toast(result.message||'删除完成'); setTimeout(()=>location.reload(),450);
}
async function startJob(){
  try{await saveEmailConfig()}catch(error){toast('邮箱设置保存失败: '+error.message);return}
  const count=parseInt(document.getElementById('count').value||'1',10);
  try{const result=await api('/api/job/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count})});toast(result.message||'已启动');poll()}catch(error){toast('启动失败: '+error.message)}
}
async function stopJob(){try{const result=await api('/api/job/stop',{method:'POST'});toast(result.message||'已停止')}catch(error){toast('停止失败: '+error.message)} }
async function backfillCpa(){try{const result=await api('/api/cpa/backfill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({limit:200})});toast(result.message||'已重新扫描');poll()}catch(error){toast('重新扫描失败: '+error.message)} }
function clearLogView(){document.getElementById('logbox').textContent='';lastLogLen=0}
async function poll(){
  try{
    const result=await api('/api/job/status'); const job=result.job||{}; const cpa=result.cpa||{};
    document.getElementById('st_status').textContent=job.status||'idle';
    document.getElementById('st_dot').className='status-dot'+(job.running?' run':'');
    document.getElementById('st_success').textContent=String(job.success||0); document.getElementById('st_fail').textContent=String(job.fail||0);
    document.getElementById('st_sf').textContent=`${job.success||0} / ${job.fail||0}`; document.getElementById('st_round').textContent=`${job.current_round||0} / ${job.count||0}`;
    document.getElementById('btn_start').disabled=!!job.running; document.getElementById('btn_stop').disabled=!job.running;
    const badge=document.getElementById('job_badge'); badge.textContent=job.status==='converting_cpa'?'转换 CPA':(job.running?'运行中':'待命'); badge.className='pill '+(job.running?'':'neutral');
    document.getElementById('st_cpa_ok').textContent=String(cpa.files||0); document.getElementById('st_cpa_q').textContent=`${cpa.pending||0}待 / ${cpa.ok||0}成 / ${cpa.fail||0}败`;
    const core=cpa.core_ok?'转换核心就绪':'转换核心异常'; const last=cpa.last_ok_email?` · 最近 ${cpa.last_ok_email}`:''; const error=cpa.last_error?` · ${cpa.last_error}`:'';
    document.getElementById('cpa_hint').textContent=`自动 CPA ${cpa.enabled?'已开启':'已关闭'} · ${core} · ${cpa.files||0} 个文件${last}${error}`;
    const logs=result.logs||[]; if(logs.length!==lastLogLen){const box=document.getElementById('logbox');box.textContent=logs.join('\n')||'等待任务';box.scrollTop=box.scrollHeight;lastLogLen=logs.length}
  }catch(_error){}
}
document.addEventListener('DOMContentLoaded',()=>{
  renderIcons(); loadEmailConfig(); poll(); setInterval(poll,2000);
  document.querySelectorAll('.nav-item').forEach(item=>item.addEventListener('click',()=>{document.querySelectorAll('.nav-item').forEach(x=>x.classList.remove('active'));item.classList.add('active');toggleSidebar(false)}));
});
