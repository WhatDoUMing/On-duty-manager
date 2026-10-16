'use strict';
const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const uid = () => [...crypto.getRandomValues(new Uint8Array(16))].map(b=>b.toString(16).padStart(2,'0')).join('');
const dateOf = ds => new Date(ds+'T12:00:00');
const iso = date => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
const plus = (ds, n) => { const d=dateOf(ds); d.setDate(d.getDate()+n); return iso(d); };
const human = ds => dateOf(ds).toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'});
let view=null, tab='overview', calendarMode='week', calendarAnchor='', draft=null, editing=false, step=0, editorRevision=0;
let setupToken='', feedbackTimer, calendarDays=[];

async function api(path, body) {
  const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',
    headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
  const value=await response.json();
  if(!response.ok){const error=new Error(value.error||'请求失败，请重试'); Object.assign(error,value,{status:response.status}); throw error;}
  return value;
}
function feedback(message){$('#feedback').textContent=message;$('#feedback').hidden=false;clearTimeout(feedbackTimer);feedbackTimer=setTimeout(()=>$('#feedback').hidden=true,3500);}
function formError(form,error){const node=$('.form-error',form);if(node){node.textContent=error.message||String(error);node.hidden=false;}else feedback(error.message);}
async function busy(form, run){const button=$('[type=submit]',form);if(button.disabled)return;button.disabled=true;const error=$('.form-error',form);if(error)error.hidden=true;try{await run();}catch(e){formError(form,e);}finally{button.disabled=false;}}
function ask(message,title='请核对'){
  $('#confirmTitle').textContent=title;$('#confirmText').textContent=message;
  const dialog=$('#confirmDialog');dialog.showModal();
  return new Promise(resolve=>{const finish=value=>{dialog.close();dialog.oncancel=null;$('#confirmYes').onclick=null;$('#confirmNo').onclick=null;resolve(value);};$('#confirmYes').onclick=()=>finish(true);$('#confirmNo').onclick=()=>finish(false);dialog.oncancel=event=>{event.preventDefault();finish(false);};});
}
function setScreen(name){for(const id of ['boot','loginScreen','wizard','dashboard'])$('#'+id).hidden=id!==name;}
function cfg(){return view.config||view.next_config;}
function cfgAt(ds){return [...view.state.configs].sort((a,b)=>b.effective.localeCompare(a.effective)).find(c=>c.effective<=ds);}
function fill(select, values, selected=select.value){select.innerHTML=values.map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');if(values.some(([id])=>id===selected))select.value=selected;}
function areasHTML(day){if(!day?.areas.length)return '<p class="empty">这一天尚未开始排班</p>';return day.areas.map(area=>`<div class="area-row"><div class="area-line"><span class="area-name">${esc(area.area_name)}</span>${area.group_name?`<span class="area-group">${esc(area.group_name)}</span>`:''}</div><div class="people">${area.students.map(s=>`<span class="person${s.absent?' absent':area.extra_ids?.includes(s.id)?' extra':''}">${esc(s.name)}${s.absent?' · 请假':''}</span>`).join('')||'<span class="muted">暂无出勤人员</span>'}</div>${area.students.length&&area.students.every(s=>s.absent)?'<p class="input-help">全员缺席 · 保留原排班，请核对是否需要调班</p>':''}${area.conflict?'<p class="form-error">该任务存在调班冲突，请检查。</p>':''}</div>`).join('');}
function recordsHTML(records){if(!records.length)return '<p class="empty">暂无标记，保持这份整洁。</p>';return records.map(m=>`<div class="record${m.cancelled_on?' cancelled':''}"><span class="record-icon" aria-hidden="true">${m.reason==='missed'?'!':'↘'}</span><div class="record-copy"><strong>${esc(m.target_name)}<span class="record-tag">${m.reason==='missed'?'忘做':'值日较差'}</span>${m.cancelled_on?'<span class="record-tag">已撤销</span>':''}</strong><p>${esc(m.date)} · ${esc(m.area_name)} · ${m.target_kind==='group'?'小组':'个人'} · ${m.penalty==='duty'?`额外值日 ${m.days} 天`:'值日以外的处罚'}${m.duty_cancelled?' · 剩余值日已取消':''}</p>${m.note?`<p>${esc(m.note)}</p>`:''}</div>${!m.cancelled_on?`<button class="text-button danger" data-cancel-mark="${esc(m.id)}">撤销</button>`:''}</div>`).join('');}

async function mutate(operation, body){
  let payload={...body,revision:view.revision,request_id:uid()};
  try{
    let result;
    try{result=await api('/api/'+operation,payload);}catch(e){
      if(!e.conflicts)throw e;
      if(!await ask(e.conflicts.map(c=>`${c.kind==='swap'?'调班':'剩余处罚'}：${c.label}`).join('\n')+'\n\n确认后将同时撤销这些冲突项，考核记录保留。','调整存在冲突'))return null;
      result=await api('/api/'+operation,{...payload,cancel_conflicts:true});
    }
    view={...result,lan_urls:result.lan_urls||view.lan_urls};await render();return result;
  }catch(e){if(e.stale){await load(false);e.message+='。表单内容已保留，请核对最新数据后重试。';}throw e;}
}
async function load(initial=true){try{const result=await api('/api/state');view=result;if(!calendarAnchor)calendarAnchor=view.today;if(!$('#reportDate').value)$('#reportDate').value=view.today;await render();if(initial)setScreen('dashboard');}catch(e){if(e.status===401)setScreen('loginScreen');else{if(view)$('#syncStatus').textContent='连接中断，正在重试';else{setScreen('loginScreen');formError($('#loginForm'),e);}}}}
async function render(){
  const c=cfg();if(!c)return;
  $('#classHeading').textContent=c.class_name;
  $('#todayHeading').textContent=human(view.today)+(view.next_config?` · 新配置将于 ${view.next_config.effective} 生效`:' · 每个角落，都有清晰安排');
  $('#todayDate').textContent=view.today.slice(5).replace('-',' / ');$('#tomorrowDate').textContent=view.tomorrow.slice(5).replace('-',' / ');
  $('#todayAreas').innerHTML=areasHTML(view.today_duty);$('#tomorrowAreas').innerHTML=areasHTML(view.tomorrow_duty);
  const settings=view.state.settings;
  $('#reminderNote').textContent=settings.paused?'每日提醒已暂停':`每天 ${settings.reminder_time} 静音预告`;
  $('#reminderTime').textContent=settings.reminder_time;$('#reminderModeNote').textContent=settings.paused?'提醒已暂停':settings.reminder_mode==='popup'?'静音弹窗 · 预告明日':'浏览器展示页 · 预告明日';
  $('#areaCount').textContent=c.areas.length;$('#modeNote').textContent=c.mode==='group'?'各区域独立小组轮换':'各区域按顺序整批轮换';
  $('#recentMarks').innerHTML=recordsHTML(view.state.marks.slice(-5).reverse());
  const warning=$('#overlapBanner');warning.hidden=!view.overlaps.length;
  if(view.overlaps.length)warning.innerHTML=`<span class="warning-icon">!</span><div class="warning-copy"><strong>发现 ${view.overlaps.length} 处跨组人员交叉</strong>${view.overlaps.slice(0,3).map(w=>`<p>${esc(w.name)}：${w.groups.map(g=>esc(g.area+' / '+g.name)).join(' ↔ ')}</p>`).join('')}${view.overlaps.length>3?'<p>请在确认窗口查看完整列表。</p>':''}</div><button class="button" id="confirmOverlaps">核对并确认</button>`;
  const areaOptions=[['','全部区域'],...c.areas.map(a=>[a.id,a.name])];
  fill($('#calendarArea'),areaOptions);
  const historical=new Map();for(const version of view.state.configs)for(const area of version.areas)historical.set(area.id,area.name);
  fill($('#reportArea'),[['','全部区域'],...[...historical].map(([id,name])=>[id,name])]);
  const away=new Set(view.away_ids),pending=new Set(view.pending_returns);
  $('#studentList').innerHTML=c.students.map(s=>`<div class="student-row"><span class="avatar">${esc(s.number||s.name.slice(-2))}</span><div class="student-copy">${esc(s.name)}<small>${c.areas.filter(a=>a.student_ids?.includes(s.id)||a.groups?.some(g=>g.student_ids.includes(s.id))).map(a=>esc(a.name)).join(' · ')||'尚未分配区域'}${pending.has(s.id)?' · 明日起返校':''}</small></div><button class="text-button" data-absence="${esc(s.id)}" data-away="${away.has(s.id)?'false':'true'}" ${pending.has(s.id)?'disabled':''}>${pending.has(s.id)?'待返校':away.has(s.id)?'返校':'请假'}</button></div>`).join('');
  const studentName=id=>c.students.find(s=>s.id===id)?.name||id;
  $('#areaSettings').innerHTML=c.areas.map(a=>`<div class="settings-area"><h3>${esc(a.name)}</h3><p>${a.cycle==='weekly'?'按周轮换 · '+(a.week_policy==='calendar'?'固定周一交接':'处罚后整体顺延'):'每日轮换'}</p>${a.groups?a.groups.map(g=>`<p>${esc(g.name)}：${g.student_ids.map(id=>esc(studentName(id))).join('、')}</p>`).join(''):`<p>每次 ${a.count} 人 · ${a.student_ids.map(id=>esc(studentName(id))).join(' → ')}</p>`}</div>`).join('');
  const swaps=view.state.swaps.filter(s=>!s.cancelled_from);
  $('#swapList').innerHTML=swaps.length?swaps.slice().reverse().map(s=>`<div class="record"><div class="record-copy"><strong>${esc(c.areas.find(a=>a.id===s.area_id)?.name||'历史区域')}</strong><p>${esc(s.first_date)} ↔ ${esc(s.second_date)}</p><p>原选日期，顺延后的实际日期请查看日历。</p></div><button class="text-button danger" data-cancel-swap="${esc(s.id)}">撤销</button></div>`).join(''):'<p class="empty">暂无调班记录</p>';
  $('#syncStatus').textContent='已同步';$('#lanInfo').textContent=view.lan_urls?.length?'手机管理：'+view.lan_urls.join(' · '):'本机与手机共享同一份排班';
  try{const report=await api('/api/assessments?period=week&anchor='+view.today);$('#weekCount').textContent=report.details.length;}catch(e){$('#syncStatus').textContent='考核同步待重试';}
  if(tab==='calendar')await renderCalendar();if(tab==='assessment')await renderReport();
}

async function selectTab(name){tab=name;$$('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===name)));$$('[data-panel]').forEach(p=>p.hidden=p.dataset.panel!==name);if(name==='calendar')await renderCalendar();if(name==='assessment')await renderReport();}
async function renderCalendar(){
  let start,end;const anchor=dateOf(calendarAnchor);
  if(calendarMode==='month'){const first=new Date(anchor.getFullYear(),anchor.getMonth(),1,12);start=plus(iso(first),-((first.getDay()+6)%7));end=plus(start,41);$('#calendarRange').textContent=`${anchor.getFullYear()} 年 ${anchor.getMonth()+1} 月`;}
  else{start=plus(calendarAnchor,-((anchor.getDay()+6)%7));end=plus(start,6);$('#calendarRange').textContent=start.slice(5)+' — '+end.slice(5);}
  const result=await api(`/api/schedule?start=${start}&end=${end}`);calendarDays=result.days;
  const area=$('#calendarArea').value;
  $('#calendar').innerHTML='<div class="calendar-weekdays">'+['一','二','三','四','五','六','日'].map(d=>`<span>周${d}</span>`).join('')+`</div><div class="calendar-grid ${calendarMode}">`+result.days.map(d=>`<button class="calendar-day${d.date===view.today?' is-today':''}${calendarMode==='month'&&dateOf(d.date).getMonth()!==anchor.getMonth()?' outside':''}" data-day="${d.date}" aria-label="${d.date} 值日安排"><span class="day-number">${Number(d.date.slice(-2))}${d.date===view.today?'<small>今日</small>':''}</span>${d.areas.filter(a=>!area||a.area_id===area).slice(0,calendarMode==='week'?30:3).map(a=>`<span class="day-area"><strong>${esc(a.area_name)}</strong>${esc(a.group_name||a.students.filter(s=>!s.absent).map(s=>s.name).join('、')||'缺员')}${a.is_group_penalty?' · 补做':''}</span>`).join('')}${calendarMode==='month'&&d.areas.filter(a=>!area||a.area_id===area).length>3?'<span class="input-help">更多安排 ↗</span>':''}</button>`).join('')+'</div>';
}
function reportTargets(){
  const objects=new Map(),kind=$('#reportKind').value,aid=$('#reportArea').value;
  for(const version of view.state.configs)for(const area of version.areas){
    if(aid&&area.id!==aid)continue;
    if(kind!=='student')for(const group of area.groups||[])objects.set('group:'+group.id,area.name+' / '+group.name);
    if(kind!=='group'){
      const roster=new Set(area.student_ids||(area.groups||[]).flatMap(g=>g.student_ids));
      for(const student of version.students)if(roster.has(student.id))objects.set('student:'+student.id,student.name+(student.number?' · '+student.number:''));
    }
  }
  fill($('#reportTarget'),[['','全部对象'],...[...objects]]);
}
function reportQuery(){const selected=$('#reportTarget').value.split(':');return new URLSearchParams({period:$('#reportPeriod').value,anchor:$('#reportDate').value||view.today,area_id:$('#reportArea').value,target_kind:selected.length===2?selected[0]:$('#reportKind').value,target_id:selected[1]||'',reason:$('#reportReason').value});}
async function renderReport(){reportTargets();const report=await api('/api/assessments?'+reportQuery());$('#reportRange').textContent=`${report.start} 至 ${report.end} · 共 ${report.details.length} 次标记`;
  $('#reportSummary').innerHTML=report.summary.length?'<table><thead><tr><th>对象</th><th>类型 / 区域</th><th>忘做</th><th>较差</th><th>总次数</th></tr></thead><tbody>'+report.summary.sort((a,b)=>b.total-a.total).map(r=>`<tr><td>${esc(r.target_name)}</td><td>${r.target_kind==='group'?'小组':'个人'} · ${esc(r.area_name)}</td><td>${r.missed}</td><td>${r.poor}</td><td class="count-cell">${r.total}</td></tr>`).join('')+'</tbody></table>':'<p class="empty">这段时间没有符合条件的考核记录。</p>';
  $('#reportDetails').innerHTML=recordsHTML(report.details.slice().reverse());
}
function download(name,content,type){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

// The editor and initial wizard share the same native form controls.
const initialSteps=['轮换方式','班级与学生','区域与顺序','提醒设置','核对保存'];
const editSteps=['轮换方式','班级与学生','区域与顺序','核对保存'];
function newArea(name='教室清扫',autofill=false){const ids=draft.students.map(s=>s.id);const half=Math.ceil(ids.length/2);return {id:uid(),name,cycle:'daily',week_policy:'shift',...(draft.mode==='group'?{groups:[{id:uid(),name:'第一组',student_ids:autofill?ids.slice(0,half):[]},...(ids.length>1?[{id:uid(),name:'第二组',student_ids:autofill?ids.slice(half):[]}]:[])]}:{count:Math.min(2,Math.max(1,ids.length)),student_ids:ids})};}
function startWizard(isEditing=false){editing=isEditing;step=0;editorRevision=view?.revision||0;draft=isEditing?structuredClone(view.next_config||view.config):{mode:'group',class_name:'',students:[],areas:[],effective:iso(new Date()),settings:{reminder_time:'22:00',reminder_mode:'popup',widget_visible:true}};$('#wizardCancel').hidden=!editing;$('#wizardTitle').innerHTML=editing?'调整安排，<br>从明天开始生效。':'一起照顾好<br>班级的每个角落。';setScreen('wizard');renderStep();}
function parseStudents(raw){const existing=draft.students||[];const used=new Set();const lines=raw.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);if(!lines.length||lines.length>300)throw new Error('请输入 1 至 300 位学生，每行一人。');return lines.map(line=>{const match=line.match(/^([A-Za-z0-9_-]+)[\s,，]+(.+)$/);const number=match?match[1]:'';const name=(match?match[2]:line).trim();if(name.length>40||number.length>40)throw new Error('姓名与学号均请控制在 40 字以内。');const key=number?'number:'+number:'name:'+name;if(used.has(key))throw new Error('名单存在重复；同名学生请填写不同学号。');used.add(key);const old=existing.find(s=>number?s.number===number:s.name===name&&!s.number);return {id:old?.id||uid(),name,number};});}
function tokenName(s){return s.number||s.name;}
function resolveOrder(raw){const tokens=raw.split(/[\s,，、;；]+/).filter(Boolean);const result=tokens.map(token=>{const matches=draft.students.filter(s=>s.number===token||s.name===token);if(matches.length!==1)throw new Error(`“${token}”未匹配唯一学生，请使用学号区分同名学生。`);return matches[0].id;});if(!result.length||new Set(result).size!==result.length)throw new Error('区域排序需包含至少一人，且人员应唯一。');return result;}
function readAreas(validate=true){return $$('.area-editor',$('#wizardBody')).map(card=>{const value={id:card.dataset.area,name:$('[data-name]',card).value.trim(),cycle:$('[data-cycle]',card).value,week_policy:$('[data-policy]',card).value};if(validate&&!value.name)throw new Error('请填写每个区域的名称。');if(draft.mode==='group'){value.groups=$$('.group-editor',card).map(g=>({id:g.dataset.group,name:$('[data-group-name]',g).value.trim(),student_ids:$$('input[data-member]:checked',g).map(x=>x.value)}));if(validate&&(!value.groups.length||value.groups.some(g=>!g.name||!g.student_ids.length)))throw new Error('每个区域至少有一个小组，请填写组名并选择组员。');}else{value.student_ids=validate?resolveOrder($('[data-order]',card).value):($('[data-order]',card).value.split(/[\s,，、]+/).filter(Boolean).flatMap(t=>draft.students.filter(s=>s.number===t||s.name===t).map(s=>s.id)));value.count=Number($('[data-count]',card).value);if(validate&&(!Number.isInteger(value.count)||value.count<1||value.count>value.student_ids.length))throw new Error('每日人数应在 1 至该区域名单人数之间。');}return value;});}
function readStep(validate=true){
  if(step===0){const mode=$('input[name=mode]:checked').value;if(draft.mode!==mode){draft.mode=mode;draft.areas=[];}}
  if(step===1){draft.class_name=$('#className').value.trim();draft.effective=$('#startDate').value;if(validate&&!draft.class_name)throw new Error('请填写班级名称。');const students=parseStudents($('#rosterInput').value);const valid=new Set(students.map(s=>s.id));draft.students=students;for(const area of draft.areas){if(area.student_ids)area.student_ids=area.student_ids.filter(s=>valid.has(s));for(const group of area.groups||[])group.student_ids=group.student_ids.filter(s=>valid.has(s));}if(!draft.areas.length)draft.areas=[newArea('教室清扫',true)];}
  if(step===2){draft.areas=readAreas(validate);if(validate&&!draft.areas.length)throw new Error('请添加至少一个值日区域。');}
  if(step===3&&!editing)draft.settings={reminder_time:$('#setupTime').value,reminder_mode:$('#setupMode').value,widget_visible:$('#setupWidget').checked};
}
function areaEditors(){
  return draft.areas.map(a=>`<section class="area-editor" data-area="${a.id}">
    <div class="area-editor-top"><input data-name value="${esc(a.name)}" maxlength="40" aria-label="区域名称" required><button type="button" class="text-button danger" data-remove-area="${a.id}">移除</button></div>
    <div class="form-grid"><label>轮换周期<select data-cycle><option value="daily" ${a.cycle==='daily'?'selected':''}>每日轮换</option><option value="weekly" ${a.cycle==='weekly'?'selected':''}>每周轮换</option></select></label><label data-policy-label ${a.cycle==='daily'?'hidden':''}>按周遇到小组处罚<select data-policy><option value="shift" ${a.week_policy==='shift'?'selected':''}>后续整体顺延，每组满七天</option><option value="calendar" ${a.week_policy==='calendar'?'selected':''}>保持自然周，每周一交接</option></select></label></div>
    ${draft.mode==='group'?(a.groups||[]).map((g,i)=>`<div class="group-editor" data-group="${g.id}">
      <div class="group-top"><input data-group-name value="${esc(g.name)}" maxlength="40" aria-label="小组名称" required><button type="button" class="icon-button reorder" data-group-up="${g.id}" aria-label="上移小组" ${i===0?'disabled':''}>↑</button><button type="button" class="icon-button reorder" data-group-down="${g.id}" aria-label="下移小组" ${i===a.groups.length-1?'disabled':''}>↓</button><button type="button" class="text-button danger" data-remove-group="${g.id}">移除</button></div>
      <details class="member-picker"><summary>选择组员 · 已选 ${g.student_ids.length} 人</summary><div class="student-choices">${draft.students.map(s=>`<label><input type="checkbox" data-member value="${s.id}" ${g.student_ids.includes(s.id)?'checked':''}>${esc(s.number?s.number+' '+s.name:s.name)}</label>`).join('')}</div></details></div>`).join('')+`<button type="button" class="text-button" data-add-group="${a.id}">＋ 添加小组</button>`:
      `<label>每次需要几人<input type="number" data-count min="1" max="${draft.students.length}" value="${a.count}" required></label><label>该区域人员顺序<textarea data-order rows="3" required>${esc(a.student_ids.map(id=>draft.students.find(s=>s.id===id)).filter(Boolean).map(tokenName).join('、'))}</textarea></label><p class="input-help">按学号或姓名排列，用空格、逗号或顿号分隔；同名学生请用学号。</p>`}
    </section>`).join('')+'<button type="button" id="addArea" class="button full">＋ 添加值日区域</button>';
}
function summaryHTML(){return `<div class="wizard-summary"><h3>${esc(draft.class_name)}</h3><p>${draft.students.length} 位学生 · ${draft.areas.length} 个区域 · ${draft.mode==='group'?'小组轮换':'个人批次轮换'}</p><p>${editing?'新配置从 '+view.tomorrow+' 生效，历史保留':'从 '+esc(draft.effective)+' 开始，包含周末'}</p>${draft.areas.map(a=>`<div class="review-area"><strong>${esc(a.name)}</strong><span>${a.cycle==='daily'?'每日轮换':'每周轮换 · '+(a.week_policy==='shift'?'整体顺延':'自然周交接')} · ${a.groups?`${a.groups.length} 个小组`:`每次 ${a.count} 人`}</span></div>`).join('')}</div>`;}
function renderStep(){
  const steps=editing?editSteps:initialSteps;
  $('#wizardError').hidden=true;$('#stepNumber').textContent=String(step+1).padStart(2,'0')+' / 0'+steps.length;$('#stepTitle').textContent=steps[step];$('#stepHint').textContent=['先选适合班级的方式，之后也可以调整。','名单是所有区域共同的学生库，学号为选填。','各区域独立轮转，顺序从上到下。',editing?'保存前会检查调班及未完成处罚冲突。':'每天安静预告明日，桌面常驻显示今日。','最后核对安排，并设置管理口令。'][step];
  $('#wizardSteps').innerHTML=steps.map((name,i)=>`<li class="${i===step?'active':i<step?'done':''}"><span>${i<step?'✓':String(i+1).padStart(2,'0')}</span>${name}</li>`).join('');
  $('#wizardBack').hidden=step===0;$('#wizardNext').textContent=step===steps.length-1?(editing?'预览并保存':'完成初始化'):'下一步 →';
  let html='';
  if(step===0)html=`<div class="mode-options"><label class="mode-option"><input type="radio" name="mode" value="group" ${draft.mode==='group'?'checked':''}><span><strong>固定小组轮流负责</strong><small>各区域分别建立小组和顺序，人员交叉会提醒。适合按卫生小组分工的班级。</small></span></label><label class="mode-option"><input type="radio" name="mode" value="individual" ${draft.mode==='individual'?'checked':''}><span><strong>按个人顺序轮流</strong><small>各区域设置人数与名单，按顺序整批轮换。适合逐人安排、没有固定小组的班级。</small></span></label></div>`;
  if(step===1)html=`<div class="form-grid"><label>班级名称<input id="className" value="${esc(draft.class_name)}" placeholder="例如：高一（1）班" maxlength="40" required></label><label>${editing?'生效日期':'开始日期'}<input id="startDate" type="date" value="${editing?view.tomorrow:esc(draft.effective)}" ${editing?'disabled':''} required></label></div><label>学生名单<textarea id="rosterInput" rows="10" placeholder="01 林予安&#10;02 陈星野&#10;03 沈知夏&#10;也可以只填姓名，每行一人" required>${esc(draft.students.map(s=>s.number?s.number+' '+s.name:s.name).join('\n'))}</textarea></label><p class="input-help">支持从表格粘贴“学号 姓名”两列；同名学生请填写不同学号。</p>`;
  if(step===2)html=areaEditors();
  if(step===3&&!editing)html=`<label>每日提醒时间<input id="setupTime" type="time" value="${esc(draft.settings.reminder_time)}" required></label><label>提醒方式<select id="setupMode"><option value="popup" ${draft.settings.reminder_mode==='popup'?'selected':''}>静音弹窗</option><option value="display" ${draft.settings.reminder_mode==='display'?'selected':''}>浏览器展示页</option></select></label><label class="check-row"><input id="setupWidget" type="checkbox" ${draft.settings.widget_visible?'checked':''}>显示右上角今日桌面名单</label><p class="hint">提醒显示明日安排，无提示音。今日名单默认沉底，课件等窗口可直接覆盖；开机自启动默认关闭。</p>`;
  if(step===steps.length-1)html=summaryHTML()+(editing?'<p class="hint">人员交叉会在管理首页显示红色提醒。保存前将预览明日安排与冲突，确认后才生效。</p>':'<label>管理口令<input id="setupPassword" type="password" minlength="8" maxlength="128" autocomplete="new-password" required></label><label>再次输入口令<input id="setupPasswordConfirm" type="password" minlength="8" maxlength="128" autocomplete="new-password" required></label><p class="hint">至少 8 位。本机与同 Wi-Fi 手机访问同一份数据，管理操作需要登录。</p>');
  $('#wizardBody').innerHTML=html;window.scrollTo({top:0,behavior:'instant'});
}

$('#wizardForm').addEventListener('submit',event=>{event.preventDefault();busy(event.currentTarget,async()=>{
  const steps=editing?editSteps:initialSteps;
  if(step<steps.length-1){readStep();step++;renderStep();return;}
  if(editing){
    if(view.revision!==editorRevision)throw new Error('其他终端已修改排班。请返回管理后重新打开配置，载入最新内容。');
    const preview=await api('/api/config/preview',{revision:editorRevision,config:draft});
    const info=preview.tomorrow.areas.map(a=>`${a.area_name}：${a.group_name||a.students.map(s=>s.name).join('、')}`).join('\n');
    const conflict=preview.conflicts.length?'\n\n需同时撤销：\n'+preview.conflicts.map(c=>`${c.kind==='swap'?'调班':'剩余处罚'}：${c.label}`).join('\n'):'';
    if(!await ask(`从 ${preview.config.effective} 起生效，过去的安排保留。\n\n明日安排：\n${info}${conflict}`,'核对配置预览'))return;
    const result=await mutate('config',{config:draft,cancel_conflicts:!!preview.conflicts.length});if(!result)return;
    setScreen('dashboard');feedback('新配置已保存，次日起生效。');
  }else{
    if($('#setupPassword').value!==$('#setupPasswordConfirm').value)throw new Error('两次口令不一致。');
    await api('/api/setup',{token:setupToken,password:$('#setupPassword').value,config:draft,effective:draft.effective,settings:draft.settings});
    setupToken='';$('#wizardBody').replaceChildren();await load();feedback('班级已就绪，今日与明日安排已生成。');
  }
});});
$('#wizardBack').onclick=()=>{try{readStep(false);}catch(e){}step--;renderStep();};
$('#wizardCancel').onclick=async()=>{setScreen('dashboard');await load(false);};
$('#wizardBody').addEventListener('change',event=>{const card=event.target.closest('.area-editor');if(event.target.matches('[data-cycle]'))$('[data-policy-label]',card).hidden=event.target.value!=='weekly';if(event.target.matches('[data-member]')){const picker=event.target.closest('details');$('summary',picker).textContent='选择组员 · 已选 '+$$('input:checked',picker).length+' 人';}});
$('#wizardBody').addEventListener('click',event=>{const target=event.target.closest('button');if(!target)return;try{draft.areas=readAreas(false);if(target.id==='addArea')draft.areas.push(newArea('区域 '+(draft.areas.length+1)));if(target.dataset.removeArea)draft.areas=draft.areas.filter(a=>a.id!==target.dataset.removeArea);if(target.dataset.addGroup){const area=draft.areas.find(a=>a.id===target.dataset.addGroup);area.groups.push({id:uid(),name:'第 '+(area.groups.length+1)+' 组',student_ids:[]});}if(target.dataset.removeGroup)for(const area of draft.areas)area.groups=area.groups.filter(g=>g.id!==target.dataset.removeGroup);
  const moving=target.dataset.groupUp||target.dataset.groupDown;
  if(moving){const area=draft.areas.find(a=>a.groups?.some(g=>g.id===moving));const index=area.groups.findIndex(g=>g.id===moving),next=index+(target.dataset.groupUp?-1:1);if(next>=0&&next<area.groups.length)[area.groups[index],area.groups[next]]=[area.groups[next],area.groups[index]];}
  renderStep();}catch(e){formError($('#wizardForm'),e);}});

function openMark(){if(!view.config){feedback('排班尚未开始，开始后即可记录考核。');return;}$('#markForm').reset();$('#markDate').value=view.today;$('#markDate').max=view.today;$('#markDate').min=view.state.configs[0].effective;markAreas();$('#markDaysLabel').hidden=false;$('#penaltyHint').textContent='处罚从次日起执行，标记当天不计入额外天数。小组处罚排队，个人处罚额外加入。';$('.form-error',$('#markForm')).hidden=true;$('#markDialog').showModal();}
function markAreas(){const c=cfgAt($('#markDate').value);fill($('#markArea'),(c?.areas||[]).map(a=>[a.id,a.name]));markTargets();}
function markTargets(){const c=cfgAt($('#markDate').value);if(!c)return;const area=c.areas.find(a=>a.id===$('#markArea').value);const groupOption=$('option[value=group]',$('#markKind'));groupOption.disabled=c.mode!=='group';if(c.mode!=='group')$('#markKind').value='student';const ids=area?.student_ids||[...new Set((area?.groups||[]).flatMap(g=>g.student_ids))];const values=$('#markKind').value==='group'?(area?.groups||[]).map(g=>[g.id,g.name]):c.students.filter(s=>ids.includes(s.id)).map(s=>[s.id,s.number?s.number+' '+s.name:s.name]);fill($('#markTarget'),values);}
function openSwap(){fill($('#swapArea'),cfg().areas.map(a=>[a.id,a.name]));for(const id of ['swapFirst','swapSecond'])$('#'+id).min=view.tomorrow;$('#swapFirst').value=view.tomorrow;$('#swapSecond').value=plus(view.tomorrow,1);$('.form-error',$('#swapForm')).hidden=true;$('#swapDialog').showModal();}
function openSettings(){const s=view.state.settings;$('#reminderInput').value=s.reminder_time;$('#reminderMode').value=s.reminder_mode;$('#widgetVisible').checked=s.widget_visible;$('#pauseReminders').checked=s.paused;$('#autostart').checked=view.autostart;$('.form-error',$('#settingsForm')).hidden=true;$('#settingsDialog').showModal();}
$('#markArea').onchange=markTargets;$('#markKind').onchange=markTargets;$('#markDate').onchange=markAreas;
$('#markPenalty').onchange=()=>{$('#markDaysLabel').hidden=$('#markPenalty').value==='other';$('#penaltyHint').textContent=$('#markPenalty').value==='other'?'此项只记录考核，不改变排班。':'处罚从次日起执行，标记当天不计入额外天数。小组处罚排队，个人处罚额外加入。';};
$('#markForm').onsubmit=event=>{event.preventDefault();busy(event.currentTarget,async()=>{const result=await mutate('marks',{area_id:$('#markArea').value,date:$('#markDate').value,target_kind:$('#markKind').value,target_id:$('#markTarget').value,reason:$('#markReason').value,penalty:$('#markPenalty').value,days:Number($('#markDays').value),note:$('#markNote').value});if(result){$('#markDialog').close();feedback('考核已记录，排班已重新计算。');}});};
$('#swapForm').onsubmit=event=>{event.preventDefault();busy(event.currentTarget,async()=>{const result=await mutate('swaps',{area_id:$('#swapArea').value,first:$('#swapFirst').value,second:$('#swapSecond').value});if(result){$('#swapDialog').close();feedback('调班已保存。');}});};
$('#settingsForm').onsubmit=event=>{event.preventDefault();busy(event.currentTarget,async()=>{await mutate('settings',{reminder_time:$('#reminderInput').value,reminder_mode:$('#reminderMode').value,widget_visible:$('#widgetVisible').checked,paused:$('#pauseReminders').checked,autostart:$('#autostart').checked});$('#settingsDialog').close();feedback('设置已保存。');});};
$('#loginForm').onsubmit=event=>{event.preventDefault();busy(event.currentTarget,async()=>{await api('/api/login',{password:$('#loginPassword').value});$('#loginPassword').value='';await load();});};
$('#importFile').onchange=async()=>{try{const file=$('#importFile').files[0];if(!file)return;if(file.size>500000)throw new Error('配置文件请控制在 500 KB 内。');$('#importText').value=await file.text();}catch(e){formError($('#importForm'),e);}};
$('#importForm').onsubmit=event=>{event.preventDefault();busy(event.currentTarget,async()=>{const config=JSON.parse($('#importText').value);const preview=await api('/api/config/preview',{revision:view.revision,config});if(!await ask(`${preview.config.class_name} · ${preview.config.students.length} 位学生 · ${preview.config.areas.length} 个区域\n从 ${preview.config.effective} 起生效。${preview.conflicts.length?'\n冲突将另外显示，确认后再保存。':''}`,'核对导入配置'))return;const result=await mutate('import',{config});if(result){$('#importDialog').close();feedback('配置已导入，次日起生效。');}});};

document.addEventListener('click',async event=>{const target=event.target.closest('button');if(!target)return;try{
  if(target.hasAttribute('data-close'))target.closest('dialog').close();
  if(target.dataset.open==='mark')openMark();if(target.dataset.open==='swap')openSwap();if(target.dataset.tab)await selectTab(target.dataset.tab);
  if(target.dataset.day){const d=calendarDays.find(d=>d.date===target.dataset.day);$('#dayTitle').textContent=human(d.date);$('#dayBody').innerHTML=areasHTML(d);$('#dayDialog').showModal();}
  if(target.dataset.cancelMark&&await ask('撤销后不再计入考核，尚未执行的处罚从次日起取消。已过去和当天的安排保留。','撤销此条标记')){await mutate('marks/cancel',{id:target.dataset.cancelMark});feedback('标记已撤销。');}
  if(target.dataset.cancelSwap&&await ask('次日起取消此调班，过去的安排保留。','撤销调班'))await mutate('swaps/cancel',{id:target.dataset.cancelSwap});
  if(target.dataset.absence){target.disabled=true;try{await mutate('absence',{student_id:target.dataset.absence,away:target.dataset.away==='true'});feedback(target.dataset.away==='true'?'已标记请假。':'已安排次日返校。');}finally{target.disabled=false;}}
  if(target.id==='confirmOverlaps'){if(await ask(view.overlaps.map(w=>`${w.name}：${w.groups.map(g=>g.area+' / '+g.name).join(' ↔ ')}`).join('\n')+'\n\n确认这些跨组安排后，当前提醒将清除。','核对人员交叉')){await mutate('overlaps/confirm',{keys:view.overlaps.map(w=>w.key)});feedback('交叉安排已确认。');}}
}catch(e){feedback(e.message);}});
$('#configOpen').onclick=()=>startWizard(true);$('#settingsOpen').onclick=openSettings;$('#refresh').onclick=()=>load(false);
$('#importOpen').onclick=()=>{$('.form-error',$('#importForm')).hidden=true;$('#importDialog').showModal();};
$('#configExport').onclick=async()=>{try{download('校园值日配置-'+view.today+'.json',JSON.stringify(await api('/api/export'),null,2),'application/json;charset=utf-8');}catch(e){feedback(e.message);}};
$('#csvExport').onclick=async()=>{try{const response=await fetch('/api/assessments.csv?'+reportQuery());if(!response.ok)throw new Error('导出失败，请重新登录或检查筛选日期。');download('值日考核-'+$('#reportDate').value+'.csv',await response.arrayBuffer(),'text/csv;charset=utf-8');}catch(e){feedback(e.message);}};
for(const id of ['reportPeriod','reportDate','reportArea','reportKind','reportTarget','reportReason'])$('#'+id).onchange=()=>renderReport().catch(e=>feedback(e.message));
$('#calendarArea').onchange=()=>renderCalendar().catch(e=>feedback(e.message));
for(const [id,mode]of [['weekView','week'],['monthView','month']])$('#'+id).onclick=()=>{calendarMode=mode;$('#weekView').setAttribute('aria-pressed',String(mode==='week'));$('#monthView').setAttribute('aria-pressed',String(mode==='month'));renderCalendar().catch(e=>feedback(e.message));};
for(const [id,direction]of [['calendarPrev',-1],['calendarNext',1]])$('#'+id).onclick=()=>{if(calendarMode==='week')calendarAnchor=plus(calendarAnchor,7*direction);else{const d=dateOf(calendarAnchor);d.setDate(1);d.setMonth(d.getMonth()+direction);calendarAnchor=iso(d);}renderCalendar().catch(e=>feedback(e.message));};
$('#calendarToday').onclick=()=>{calendarAnchor=view.today;renderCalendar().catch(e=>feedback(e.message));};
$('#logout').onclick=async()=>{try{await api('/api/logout',{});view=null;setScreen('loginScreen');}catch(e){feedback(e.message);}};
document.addEventListener('keydown',()=>document.body.classList.add('keyboard'),{passive:true});document.addEventListener('pointerdown',()=>document.body.classList.remove('keyboard'),{passive:true});
setInterval(()=>{if(view&&!document.hidden&&!$('dialog[open]')&&$('#wizard').hidden)load(false);},30000);
(async()=>{try{const fragment=new URLSearchParams(location.hash.slice(1));setupToken=fragment.get('setup')||'';const ticket=fragment.get('ticket');history.replaceState(null,'',location.pathname);if(ticket)await api('/api/local-login',{token:ticket});const status=await api('/api/status');if(!status.initialized&&setupToken&&status.local){startWizard();return;}if(status.authenticated){await load();return;}setScreen('loginScreen');$('#localSetupHint').hidden=status.initialized;}catch(e){setScreen('loginScreen');formError($('#loginForm'),e);}})();
