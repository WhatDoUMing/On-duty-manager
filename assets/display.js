'use strict';
const $ = q => document.querySelector(q);
const esc = v => String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let payload=null, selected='tomorrow', reminder=null, timer;
async function api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),credentials:'same-origin',signal:AbortSignal.timeout(12000)});const value=await response.json();if(!response.ok)throw new Error(value.error||'连接失败');return value;}
function feedback(message){$('#feedback').textContent=message;$('#feedback').hidden=false;clearTimeout(timer);timer=setTimeout(()=>$('#feedback').hidden=true,4000);}
function render(){
  const day=payload[selected];$('#displayClass').textContent=payload.class_name;$('#displayTitle').innerHTML=(selected==='tomorrow'?'明日值日安排':'今日值日名单')+'<span class="heading-dot">.</span>';
  $('#displayDate').textContent=new Date(day.date+'T12:00:00').toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'long'})+' · '+day.areas.length+' 个值日区域';
  $('#displayAreas').innerHTML=day.areas.map((a,i)=>`<article class="display-area"><span class="area-index">AREA ${String(i+1).padStart(2,'0')}</span><h2>${esc(a.area_name)}</h2>${a.group_name?`<p class="group-name">${esc(a.group_name)}</p>`:''}<div class="display-people">${a.students.map(s=>`<span class="display-person${s.absent?' absent':''}">${esc(s.name)}${s.absent?'<small>请假</small>':''}</span>`).join('')||'<span class="muted">暂无出勤人员</span>'}</div></article>`).join('')||'<p class="empty">这一天尚未开始排班。</p>';
  $('#displayTomorrow').setAttribute('aria-pressed',String(selected==='tomorrow'));$('#displayToday').setAttribute('aria-pressed',String(selected==='today'));
  $('#displayError').hidden=true;$('#displaySync').innerHTML='<span class="status-dot"></span> 安排更新后自动同步';
}
async function refresh(){try{payload=await api('/api/display');render();if(reminder&&reminder.day!==payload.today.date){reminder=null;$('#reminderActions').hidden=true;}}catch(e){$('#displayError').textContent=e.message;$('#displayError').hidden=false;$('#displaySync').textContent='同步待重试';}}
for(const [id,value]of [['displayTomorrow','tomorrow'],['displayToday','today']])$('#'+id).onclick=()=>{selected=value;if(payload)render();};
$('#fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch(e){feedback('请使用浏览器的全屏功能。');}};
async function action(value){if(!reminder)return;const buttons=[$('#displaySnooze'),$('#displayDismiss')];buttons.forEach(b=>b.disabled=true);try{const result=await api('/api/reminder-action',{...reminder,action:value});$('#reminderActions').hidden=true;reminder=null;feedback(result.ok?(value==='snooze'?'十分钟后再次静音提醒。':'本次提醒已关闭，展示页可继续查看。'):'本次提醒已处理。');}catch(e){feedback(e.message);}finally{buttons.forEach(b=>b.disabled=false);}}
$('#displaySnooze').onclick=()=>action('snooze');$('#displayDismiss').onclick=()=>action('dismiss');
(async()=>{try{const params=new URLSearchParams(location.hash.slice(1));history.replaceState(null,'',location.pathname);if(params.get('ticket'))await api('/api/display-login',{token:params.get('ticket')});if(params.get('day')&&params.get('token'))reminder={day:params.get('day'),token:params.get('token')};await refresh();if(reminder&&payload){const result=await api('/api/reminder-action',{...reminder,action:'shown'});$('#reminderActions').hidden=!result.ok;}}catch(e){$('#displayError').textContent=e.message;$('#displayError').hidden=false;}})();
setInterval(()=>{if(!document.hidden)refresh();},30000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
