"use strict";

const $ = id => document.getElementById(id);
const localDay = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const parseDay = value => new Date(`${value}T12:00:00`);
const shiftDay = (value, count) => { const d=parseDay(value); d.setDate(d.getDate()+count); return localDay(d); };
const prettyDay = value => parseDay(value).toLocaleDateString("zh-CN",{month:"long",day:"numeric"});
const weekday = value => parseDay(value).toLocaleDateString("zh-CN",{weekday:"long"});
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let view=null, rows=new Map(), futureRows=[], calendarMode="week", calendarAnchor=null;
let authStatus=null, bootstrapToken="", loading=false, feedbackTimer=null;
const fragment = new URLSearchParams(location.hash.slice(1));
bootstrapToken=fragment.get("setup") || "";
const localTicket=fragment.get("ticket");
if(location.hash) history.replaceState(null,"",location.pathname);

document.addEventListener("pointerdown",()=>document.documentElement.classList.add("pointer-input"),{passive:true});
document.addEventListener("keydown",()=>document.documentElement.classList.remove("pointer-input"));

async function api(path,body){
  let response;
  try{
    response=await fetch(`/api/${path}`,body===undefined?{cache:"no-store"}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  }catch(_){throw new Error("连接中断，请确认电脑上的值日管家仍在运行。");}
  const data=await response.json();
  if(!response.ok){
    if(response.status===401) showAuth();
    const error=new Error(data.error || "操作失败，请重试");
    Object.assign(error,data); throw error;
  }
  return data;
}

function feedback(message){
  const dialog=document.querySelector("dialog[open]");
  if(dialog){
    let notice=dialog.querySelector(".dialog-feedback");
    if(!notice){notice=document.createElement("p");notice.className="dialog-feedback";notice.setAttribute("role","status");dialog.querySelector(".dialog-body").prepend(notice);}
    notice.textContent=message;
  }
  clearTimeout(feedbackTimer); $("feedback").textContent=message; $("feedback").classList.add("visible");
  feedbackTimer=setTimeout(()=>$("feedback").classList.remove("visible"),5000);
}

function showAuth(){
  document.querySelectorAll("dialog[open]").forEach(dialog=>dialog.close());
  $("dashboard").hidden=true; $("authScreen").hidden=false;
  const setup=authStatus && !authStatus.initialized;
  $("authTitle").textContent=setup?"先给日常安排一个家":"欢迎回到值日管家";
  $("authDescription").textContent=setup?(bootstrapToken?"设置管理口令，然后添加值日成员。":"请先从电脑托盘打开管理端，完成首次设置。") : "输入管理口令，查看与调整值日安排。";
  $("confirmPasswordGroup").hidden=!setup;
  $("confirmPassword").required=Boolean(setup);
  $("password").autocomplete=setup?"new-password":"current-password";
  $("authSubmit").textContent=setup?"设置口令并开始":"进入管理端 →";
  $("authSubmit").disabled=Boolean(setup&&!bootstrapToken);
}

$("authForm").addEventListener("submit",async event=>{
  event.preventDefault(); $("authError").textContent="";
  if(!authStatus){try{authStatus=await api("status");}catch(error){$("authError").textContent=error.message;return;}}
  const setup=!authStatus.initialized;
  if(setup&&$("password").value!==$("confirmPassword").value){$("authError").textContent="两次输入的口令请保持一致。";return;}
  $("authSubmit").disabled=true;
  try{
    await api(setup?"setup-auth":"login",{password:$("password").value,token:bootstrapToken});
    authStatus.initialized=true; bootstrapToken="";
    $("password").value="";$("confirmPassword").value="";
    await reload();
    if(!view.config) openSettings();
  }catch(error){$("authError").textContent=error.message;}
  finally{$("authSubmit").disabled=false;}
});

function calendarBounds(){
  const anchor=parseDay(calendarAnchor || view.today);
  if(calendarMode==="week"){
    anchor.setDate(anchor.getDate()-((anchor.getDay()+6)%7));
    const start=localDay(anchor);return[start,shiftDay(start,13)];
  }
  const first=new Date(anchor.getFullYear(),anchor.getMonth(),1,12);
  const last=new Date(anchor.getFullYear(),anchor.getMonth()+1,0,12);
  first.setDate(first.getDate()-((first.getDay()+6)%7));
  last.setDate(last.getDate()+(6-(last.getDay()+6)%7));
  return[localDay(first),localDay(last)];
}

async function reload(retry=true){
  if(loading)return; loading=true;
  try{
    const next=await api("state");
    view=next; calendarAnchor=calendarAnchor || view.today;
    const [start,end]=calendarBounds();
    const [calendar,future]=await Promise.all([
      api(`schedule?start=${start}&end=${end}`),
      api(`schedule?start=${view.today}&end=${shiftDay(view.today,365)}`)
    ]);
    if(calendar.revision!==view.revision||future.revision!==view.revision){
      loading=false;if(retry)return reload(false);throw new Error("排班正在更新，请稍后刷新。");
    }
    rows=new Map(calendar.days.map(row=>[row.date,row]));futureRows=future.days;
    $("authScreen").hidden=true;$("dashboard").hidden=false;
    render();$("syncStatus").textContent="排班已同步";
  }catch(error){$("syncStatus").textContent="等待重新连接";throw error;}
  finally{loading=false;}
}

function render(){
  const today=view.today_duty, yesterday=view.yesterday_duty;
  $("currentDate").textContent=`${parseDay(view.today).toLocaleDateString("zh-CN",{year:"numeric",month:"long",day:"numeric"})} · ${weekday(view.today)}`;
  $("todayName").textContent=view.config?today.name:"从一份名单开始";
  $("todayBadge").textContent=today.carried?"昨日任务 · 今日补做":today.mop?"今天也要拖地":"日常轮值";
  $("todayTasks").textContent=today.member_id?(today.mop?"倒垃圾 + 拖地 · 辛苦啦":"倒垃圾 · 今日无需拖地"):(today.name==="全员回家"?"大家都不在时，排班会暂停。":view.config?`将于 ${view.config.effective} 开始值日。`:"添加成员后，就能自动生成值日安排。");
  const mop=futureRows.find(row=>row.mop&&row.member_id);
  $("nextMopDate").textContent=mop?(mop.date===view.today?"就是今天":prettyDay(mop.date)):"暂未安排";
  $("nextMopPerson").textContent=mop?`${mop.name} · ${weekday(mop.date)}`:"恢复值日后继续安排";
  $("mopIntervalLabel").textContent=`每 ${view.config?.mop_interval || 3} 个值日天一次`;
  $("reminderTime").textContent=view.state.settings.reminder_time;
  $("reminderStatus").textContent=view.state.settings.paused?"提醒已暂停，可随时恢复":"Windows 右下角系统通知";
  $("reminderEnabled").textContent=view.state.settings.paused?"● 提醒已暂停":"● 提醒已开启";
  $("reminderEnabled").style.color=view.state.settings.paused?"#a0875a":"#6b8366";
  $("yesterdayTitle").textContent=yesterday.member_id?`昨日 ${prettyDay(view.yesterday)} · ${yesterday.name}${yesterday.mop?"（含拖地）":""}`:"昨日没有值日安排";
  $("yesterdayDescription").textContent=yesterday.missed?"已标记忘做，后续轮值与拖地日已整体顺延。":"未标记忘做时，系统按正常完成轮转。";
  $("missedButton").textContent=yesterday.missed?"撤销昨日忘做 ↶":"标记昨日忘做 ↗";
  $("missedButton").disabled=!yesterday.member_id;
  $("nextConfigNotice").hidden=!view.next_config;
  $("nextConfigNotice").textContent=view.next_config?`新的成员与排班规则将于 ${view.next_config.effective} 生效；正在补做的任务会先完成。`:"";
  $("swapOpen").disabled=!view.config;$("swapOpenBottom").disabled=!view.config;
  renderCalendar();renderMembers();renderRecords();
}

function renderCalendar(){
  const [start,end]=calendarBounds();
  const anchor=parseDay(calendarAnchor);
  $("calendarRange").textContent=calendarMode==="week"?`${prettyDay(start)} — ${prettyDay(end)}`:`${anchor.getFullYear()} 年 ${anchor.getMonth()+1} 月`;
  $("weekView").classList.toggle("selected",calendarMode==="week");$("monthView").classList.toggle("selected",calendarMode==="month");
  $("weekView").setAttribute("aria-pressed",String(calendarMode==="week"));$("monthView").setAttribute("aria-pressed",String(calendarMode==="month"));
  let cells="";
  for(let ds=start;ds<=end;ds=shiftDay(ds,1)){
    const row=rows.get(ds), current=ds===view.today;
    const outside=calendarMode==="month"&&parseDay(ds).getMonth()!==anchor.getMonth();
    const classes=["calendar-cell",row?.mop?"mop-day":"",row?.missed?"missed-day":"",current?"today":"",outside?"outside":""].filter(Boolean).join(" ");
    cells+=`<div class="${classes}" ${current?'aria-current="date"':''} aria-label="${ds} ${escapeHTML(row?.name || "未开始")}${row?.mop?" 拖地":""}"><div class="day">${parseDay(ds).getDate()}${current?"<small>今天</small>":""}</div><span class="person">${escapeHTML(row?.name || "未开始")}</span>${row?.mop?'<span class="cell-tag">拖地</span>':""}${row?.missed?'<span class="cell-tag">忘做</span>':row?.carried?'<span class="cell-tag">补做</span>':""}</div>`;
  }
  $("calendar").innerHTML=`<div class="weekdays">${["一","二","三","四","五","六","日"].map(day=>`<span>周${day}</span>`).join("")}</div><div class="calendar-grid">${cells}</div>`;
}

function renderMembers(){
  const members=view.config?.members || [],away=new Set(view.today_duty.away_ids);
  $("memberCount").textContent=members.length;
  $("members").innerHTML=members.length?members.map((member,index)=>{
    const pending=view.pending_returns[member.id],absent=away.has(member.id),today=member.id===view.today_duty.member_id;
    const status=pending?"明日起优先值日":absent?"回家中":today?"今日值日":"按序轮值";
    return `<div class="member"><span class="avatar">${String(index+1).padStart(2,"0")}</span><div class="member-info"><span class="member-name">${escapeHTML(member.name)}</span><span class="member-status ${today?'active':''}">${status}</span></div><button class="text-button" data-member="${escapeHTML(member.id)}" data-away="${pending?'true':!absent}">${pending?"取消返校":absent?"已返校":"回家"}</button></div>`;
  }).join(""):'<div class="empty-state"><strong>还没有值日成员</strong>点击“编辑”，添加大家的名字。</div>';
}

function renderRecords(){
  const names=new Map(view.state.configs.flatMap(c=>c.members.map(m=>[m.id,m.name])));
  const swaps=[...view.state.swaps].reverse();
  $("swaps").innerHTML=swaps.length?swaps.map(swap=>{
    const find=slot=>futureRows.find(row=>row.slot_id===slot)?.date;
    const first=find(swap.first_slot)||swap.first_date,second=find(swap.second_slot)||swap.second_date;
    return `<div class="record"><div><span class="record-title">${escapeHTML(names.get(swap.first_member)||"成员")} ↔ ${escapeHTML(names.get(swap.second_member)||"成员")}</span><p>${prettyDay(first)} ↔ ${prettyDay(second)}${swap.cancelled?" · 已撤销未来换班":" · 随任务顺延"}</p></div>${!swap.cancelled&&(first>=view.today||second>=view.today)?`<button class="text-button" data-cancel-swap="${escapeHTML(swap.id)}">撤销</button>`:""}</div>`;
  }).join(""):'<div class="empty-state"><strong>按原顺序，刚刚好</strong>有事需要调整时，可以临时换班。</div>';
  const events=[...view.state.events].sort((a,b)=>b.date.localeCompare(a.date)).slice(0,15);
  $("history").innerHTML=events.length?events.map(event=>`<div class="record"><div><span class="record-title">${event.kind==="missed"?"忘做 · 后续安排顺延":`${escapeHTML(names.get(event.member_id)||"成员")} · ${event.kind==="away"?"回家":"返校优先值日"}`}</span><p>${event.date}${event.kind==="return"&&event.date>view.today?" 起生效":""}</p></div><span class="muted">${event.kind==="missed"?"+1 天":event.kind==="away"?"暂时跳过":"优先"}</span></div>`).join(""):'<div class="empty-state"><strong>一切按计划进行</strong>忘做、回家和返校都会记录在这里。</div>';
}

async function mutate(operation,payload,success){
  if(loading)return;
  const body={...payload,revision:view.revision};
  try{
    try{await api(operation,body);}
    catch(error){
      if(error.conflicts&&confirm(`${error.message}。确认继续？`))await api(operation,{...body,cancel_conflicts:true});
      else throw error;
    }
    await reload();feedback(success);return true;
  }catch(error){
    if(error.stale)await reload().catch(()=>{});
    feedback(error.message);return false;
  }
}

function fillFirstMembers(selected){
  const names=$("memberNames").value.split(/[\n,，]/).map(s=>s.trim()).filter(Boolean);
  const previous=selected || $("firstMember").value;
  $("firstMember").replaceChildren(...names.map(name=>new Option(name,name)));
  if(names.includes(previous))$("firstMember").value=previous;
}

function openSettings(){
  const config=view.next_config || view.config;
  $("memberNames").value=config?config.members.map(m=>m.name).join("\n"):"";
  const next=futureRows.find(row=>row.date===shiftDay(view.today,1));
  fillFirstMembers(config?.members.find(m=>m.id===(view.next_config?config.first_member:next?.member_id || config.first_member))?.name);
  $("startDateGroup").hidden=Boolean(view.config);
  $("startDate").value=config?.effective || view.today;
  $("configEffectiveNote").textContent=view.config?"调整从次日起生效，历史记录保留；正在补做的任务先完成。":"选择开始日期和起始成员，自动生成轮值。";
  $("mopInterval").value=config?.mop_interval || 3;
  $("firstMop").value=view.config?(view.next_config?.first_mop || futureRows.find(row=>row.date>view.today&&row.mop)?.date || shiftDay(view.today,1)):view.today;
  $("reminderInput").value=view.state.settings.reminder_time;
  $("pausedInput").checked=view.state.settings.paused;$("startupInput").checked=view.autostart;
  $("settingsDialog").showModal();
}

$("memberNames").addEventListener("input",()=>fillFirstMembers());
$("startDate").addEventListener("change",()=>{if(!view.config)$("firstMop").value=$("startDate").value;});
$("configForm").addEventListener("submit",async event=>{
  event.preventDefault();
  const known=new Map((view.next_config || view.config)?.members.map(m=>[m.name,m.id]) || []);
  const members=$("memberNames").value.split(/[\n,，]/).map(s=>s.trim()).filter(Boolean).map(name=>({name,id:known.get(name)||`m_${Date.now()}_${Math.random().toString(36).slice(2)}`}));
  const payload={members,first_member:members.find(m=>m.name===$("firstMember").value)?.id,
    effective:$("startDate").value,mop_interval:Number($("mopInterval").value),first_mop:$("firstMop").value};
  if(await mutate("config",payload,view.config?"排班规则已保存，次日起生效。":"值日安排已就绪。"))$("settingsDialog").close();
});

$("reminderForm").addEventListener("submit",async event=>{
  event.preventDefault();
  if(await mutate("settings",{reminder_time:$("reminderInput").value,paused:$("pausedInput").checked,autostart:$("startupInput").checked},"提醒设置已保存。"))$("settingsDialog").close();
});
$("importForm").addEventListener("submit",async event=>{
  event.preventDefault();if(await mutate("import",{key:$("importKey").value},"原值日配置已导入。")){$("settingsDialog").close();$("importKey").value="";}
});

async function datePreview(id,output){
  const value=$(id).value;if(!value)return;
  try{const result=await api(`schedule?start=${value}&end=${value}`);const info=result.days[0];if($(id).value===value)$(output).textContent=`${info.name}${info.mop?" · 含拖地":""}${info.carried?" · 补做中":""}`;}
  catch(error){$(output).textContent=error.message;}
}
function openSwap(){
  if(!view.config)return;
  for(const id of ["swapFirst","swapSecond"]){$(id).min=view.today;$(id).max=shiftDay(view.today,730);}
  $("swapFirst").value=shiftDay(view.today,1);$("swapSecond").value=shiftDay(view.today,2);
  datePreview("swapFirst","swapFirstInfo");datePreview("swapSecond","swapSecondInfo");$("swapDialog").showModal();
}
$("swapFirst").addEventListener("change",()=>datePreview("swapFirst","swapFirstInfo"));
$("swapSecond").addEventListener("change",()=>datePreview("swapSecond","swapSecondInfo"));
$("swapForm").addEventListener("submit",async event=>{event.preventDefault();if(await mutate("swaps",{first:$("swapFirst").value,second:$("swapSecond").value},"换班已安排，顺延时会一起调整。"))$("swapDialog").close();});

$("missedButton").addEventListener("click",async()=>{
  const undo=view.yesterday_duty.missed;
  if(!undo&&!confirm(`确认 ${view.yesterday_duty.name} 昨日忘做？今日继续由这位成员负责，后续轮值和拖地一起后移一天。`))return;
  $("missedButton").disabled=true;
  await mutate("missed",{date:view.yesterday,undo},undo?"已撤销误标，排班已恢复。":"昨日已标记忘做，后续安排已整体顺延。");
  $("missedButton").disabled=!view.yesterday_duty.member_id;
});
$("members").addEventListener("click",async event=>{
  const button=event.target.closest("[data-member]");if(!button)return;
  const member=view.config.members.find(m=>m.id===button.dataset.member),away=button.dataset.away==="true";
  if(!confirm(away?`确认 ${member.name} 回家？在家期间跳过该成员。`:`确认 ${member.name} 已返校？从次日起优先值日，补做任务仍先完成。`))return;
  button.disabled=true;await mutate("absence",{member_id:member.id,away},away?"已标记回家。":"已安排次日起优先值日。");button.disabled=false;
});
$("swaps").addEventListener("click",async event=>{
  const button=event.target.closest("[data-cancel-swap]");if(!button||!confirm("确认撤销尚未执行的换班？历史已发生的安排会保留。"))return;
  await mutate("swaps/cancel",{id:button.dataset.cancelSwap},"未来换班已撤销。");
});

for(const id of ["settingsOpen","membersEdit","reminderOpen"])$(id).addEventListener("click",openSettings);
for(const id of ["swapOpen","swapOpenBottom"])$(id).addEventListener("click",openSwap);
document.querySelectorAll("[data-close]").forEach(button=>button.addEventListener("click",()=>button.closest("dialog").close()));
document.querySelectorAll("dialog").forEach(dialog=>dialog.addEventListener("close",()=>dialog.querySelector(".dialog-feedback")?.remove()));
$("shareOpen").addEventListener("click",()=>{
  $("lanAddresses").innerHTML=view.lan_urls.length?view.lan_urls.map(url=>`<div class="address"><a href="${escapeHTML(url)}" target="_blank" rel="noopener">${escapeHTML(url)}</a></div>`).join(""):'<p class="muted">暂无局域网地址，请先将电脑连接到 Wi-Fi。</p>';
  $("shareDialog").showModal();
});
$("logout").addEventListener("click",async()=>{try{await api("logout",{});showAuth();}catch(error){feedback(error.message);}});
$("refresh").addEventListener("click",()=>reload().then(()=>feedback("已更新为最新排班。")).catch(error=>feedback(error.message)));
for(const [id,mode] of [["weekView","week"],["monthView","month"]])$(id).addEventListener("click",()=>{calendarMode=mode;reload().catch(error=>feedback(error.message));});
function navigate(direction){
  const next=parseDay(calendarAnchor);if(calendarMode==="week")next.setDate(next.getDate()+14*direction);else next.setMonth(next.getMonth()+direction,1);
  if(Math.abs((next-parseDay(view.today))/86400000)>370){feedback("日历提供前后一年预览。");return;}
  calendarAnchor=localDay(next);reload().catch(error=>feedback(error.message));
}
$("previous").addEventListener("click",()=>navigate(-1));$("next").addEventListener("click",()=>navigate(1));
$("goToday").addEventListener("click",()=>{calendarAnchor=view.today;reload().catch(error=>feedback(error.message));});
setInterval(()=>{if(view&&!$("dashboard").hidden&&!document.hidden&&!document.querySelector("dialog[open]"))reload().catch(()=>{});},30000);

(async()=>{
  try{
    authStatus=await api("status");
    if(localTicket&&authStatus.initialized){await api("local-login",{token:localTicket});authStatus.authenticated=true;}
    if(authStatus.authenticated)await reload();else showAuth();
  }catch(error){showAuth();$("authError").textContent=error.message;}
})();
