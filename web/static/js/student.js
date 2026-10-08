/* 页面只保存用户选择；规则、权限、名额及最终结果由后端决定。 */
(() => {
  const root = document.getElementById('student-workspace');
  const mode = root.dataset.mode;
  const rows = document.getElementById('student-rows');
  let semester, state, items = [], dirty = false, busy = false;
  const e = SECD.escape;
  const url = path => path + '?semester_id=' + semester.semester_id;
  const core = suffix => '/semesters/' + semester.semester_id + '/schedule' + suffix;
  const normalize = () => { let n=0; items.forEach(x => x.priority = x.kind === 'alternate' ? ++n : null); };
  const body = () => ({expected_version:state.version, choices:items.map(x => ({offering_id:x.offering_id,kind:x.kind,alternate_priority:x.priority}))});
  function updateStatus() {
    document.getElementById('semester-status').textContent = state.status === 'open' ? '选课开放中' : '选课已关闭，结果只读';
    document.getElementById('save-status').textContent = dirty ? '有未保存的修改' : '当前内容已保存；版本 '+state.version;
    root.querySelectorAll('button').forEach(button => button.disabled = busy || state.status !== 'open');
  }
  function render() {
    const shown=mode==='results' ? [...state.results,...state.alternates] : items;
    rows.innerHTML=shown.length ? shown.map((x,i) => `<tr><td><strong>${e(x.course_code)}</strong> ${e(x.course_name)}<br>班号 ${e(x.section_number)}</td><td>${e(x.description)}<br>费用 ¥${e(x.fee)}<br>先修：${e((x.prerequisites||[]).join('、')||'无')}</td><td>${e(x.teacher_name)}<br>${e(x.schedule_time)}</td><td>${x.enrolled_count} / ${x.capacity}</td><td>${mode==='courses' ? `<select class="form-select" data-add-kind="${x.id}"><option value="primary">主选</option><option value="alternate">备选</option></select>` : mode==='draft' ? `<select class="form-select" data-kind-index="${i}"><option value="primary" ${x.kind==='primary'?'selected':''}>主选</option><option value="alternate" ${x.kind==='alternate'?'selected':''}>备选</option></select>${x.kind==='alternate'?'备选 #'+x.priority:''}` : x.kind==='alternate' ? '备选 #'+x.priority : '正式选上'}</td><td>${mode==='courses' ? `<button class="btn btn-sm btn-primary" data-add="${x.id}">加入草稿</button>` : mode==='draft' ? `<button class="btn btn-sm btn-secondary" data-move="${i}" data-direction="-1">↑</button> <button class="btn btn-sm btn-secondary" data-move="${i}" data-direction="1">↓</button> <button class="btn btn-sm btn-danger" data-remove="${i}">移除</button>` : x.kind==='enrolled' ? `<button class="btn btn-sm btn-danger" data-drop="${x.offering_id}">退课</button>` : '-'}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-state">当前没有记录。</td></tr>';
    updateStatus();
  }
  async function reload() {
    state=await SECD.request(url('/api/v1/student/draft'));
    if (mode==='courses') { const data=await SECD.request(url('/api/v1/student/available-sections')); items=data.sections; }
    else items=state.items;
    dirty=false; render();
  }
  async function save() {
    normalize();
    if (items.filter(x=>x.kind==='primary').length>4 || items.filter(x=>x.kind==='alternate').length>2) throw new Error('最多 4 个主选和 2 个备选');
    const result=await SECD.request(core('/draft'),{method:'PUT',body:JSON.stringify(body())});
    state.version=result.version; dirty=false; updateStatus();
  }
  async function action(fn) {
    if (busy || !state || state.status!=='open') return;
    busy=true; updateStatus();
    try { await fn(); } catch(error) { if (!error.reported) SECD.showToast(error.message,'danger'); }
    finally { busy=false; updateStatus(); }
  }
  rows.addEventListener('change',event=>{
    if (event.target.dataset.kindIndex!==undefined) { items[Number(event.target.dataset.kindIndex)].kind=event.target.value; normalize(); dirty=true; render(); }
  });
  rows.addEventListener('click',event=>{
    const button=event.target.closest('button'); if(!button) return;
    action(async()=>{
      if(button.dataset.add) {
        const selected=items.find(x=>x.id===Number(button.dataset.add));
        const kind=rows.querySelector(`[data-add-kind="${selected.id}"]`).value;
        const fresh=await SECD.request(url('/api/v1/student/draft'));
        if(fresh.items.some(x=>x.offering_id===selected.id)) throw new Error('此班次已在草稿中');
        const choices=fresh.items.map(x=>({offering_id:x.offering_id,kind:x.kind,alternate_priority:x.priority}));
        if(choices.filter(x=>x.kind===kind).length >= (kind==='primary'?4:2)) throw new Error(kind==='primary'?'主选最多 4 个':'备选最多 2 个');
        choices.push({offering_id:selected.id,kind,alternate_priority:kind==='alternate'?choices.filter(x=>x.kind==='alternate').length+1:null});
        await SECD.request(core('/draft'),{method:'PUT',body:JSON.stringify({expected_version:fresh.version,choices})});
        SECD.showToast('已保存到个人草稿'); await reload();
      } else if(button.dataset.remove!==undefined) { items.splice(Number(button.dataset.remove),1); normalize(); dirty=true; render(); }
      else if(button.dataset.move!==undefined) { const i=Number(button.dataset.move), j=i+Number(button.dataset.direction); if(j>=0&&j<items.length) { [items[i],items[j]]=[items[j],items[i]]; normalize(); dirty=true; render(); } }
      else if(button.dataset.drop && await SECD.confirmAction('确认退出此班次？主动退课不会自动用备选补回。')) { await SECD.request(core('/offerings/'+button.dataset.drop),{method:'DELETE',body:JSON.stringify({expected_version:state.version})}); await reload(); }
    });
  });
  document.getElementById('save-draft')?.addEventListener('click',()=>action(async()=>{await save(); SECD.showToast('草稿已保存');}));
  document.getElementById('submit-draft')?.addEventListener('click',()=>action(async()=>{
    if(!await SECD.confirmAction('确认保存当前草稿并提交正式选课？任意主选失败都会保留原正式结果。')) return;
    await save(); await SECD.request(core('/submit'),{method:'POST',body:JSON.stringify({expected_version:state.version})}); dirty=false;
    window.location.href='/student/results?semester_id='+semester.semester_id;
  }));
  document.getElementById('delete-schedule')?.addEventListener('click',()=>action(async()=>{
    if(await SECD.confirmAction('确认删除整份方案？将退出全部正式班次并清空草稿与备选。')) { await SECD.request(core(''),{method:'DELETE',body:JSON.stringify({expected_version:state.version})}); await reload(); }
  }));
  async function poll() {
    if(document.hidden || busy || !semester || state?.status!=='open') return;
    try {
      const fresh=await SECD.request(url('/api/v1/student/available-sections'));
      const map=new Map(fresh.sections.map(x=>[x.id,x])); let becameFull=false;
      items.forEach(x=>{const updated=map.get(x.id||x.offering_id); if(updated) {becameFull ||= x.enrolled_count<x.capacity && updated.enrolled_count>=updated.capacity; x.enrolled_count=updated.enrolled_count;x.capacity=updated.capacity;}});
      document.getElementById('capacity-notice').textContent=becameFull?'你正在查看的班次已满，请修改选择；提交时仍会重新检查名额。':'';
      render();
    } catch(error) { document.getElementById('capacity-notice').textContent='名额更新失败，当前数据可能已过期。'; }
  }
  document.addEventListener('DOMContentLoaded',async()=>{
    try {semester=await SECD.loadSemesters('semester-selector'); await reload(); if(mode!=='results') setInterval(poll,10000);}
    catch(error) {rows.innerHTML='<tr><td colspan="6">加载失败，请检查目录服务并刷新。</td></tr>';}
  });
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
})();
