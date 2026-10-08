(() => {
  let users=[], active=null;
  const e=SECD.escape, modal=document.getElementById('person-modal');
  const value=id=>document.getElementById(id).value;
  const set=(id,value)=>document.getElementById(id).value=value??'';
  async function load() {try{users=await SECD.request('/api/v1/admin/users');render();}catch(error){document.getElementById('user-list-body').textContent='资料加载失败';}}
  function render() {
    const kw=value('user-search').toLowerCase();
    const statuses={active:'在读 / 在职',on_leave:'休学 / 休假',graduated:'已毕业',retired:'已退休'};
    document.getElementById('user-list-body').innerHTML=users.filter(x=>(x.username+' '+x.full_name).toLowerCase().includes(kw)).map(x=>`<tr><td><strong>${e(x.username)}</strong><br>${e(x.full_name)}</td><td>${x.role==='student'?'学生':'教师'}<br>${e(x.department||'-')}</td><td>${e(x.birth_date||'-')}<br>${e(x.social_security_number||'-')}</td><td>${e(statuses[x.status]||x.status)}<br>${e(x.graduation_date||'-')}</td><td>${x.is_active?'已启用':'已停用'}</td><td><div class="action-row"><button class="btn btn-sm btn-outline" data-edit="${x.id}">编辑</button><button class="btn btn-sm btn-outline" data-reset="${x.id}">重置密码</button><button class="btn btn-sm btn-secondary" data-toggle="${x.id}">${x.is_active?'停用':'启用'}</button><button class="btn btn-sm btn-danger" data-delete="${x.id}">删除</button></div></td></tr>`).join('') || '<tr><td colspan="6">没有匹配的人员。</td></tr>';
  }
  function roleFields() {const teacher=value('person-role')==='teacher';document.getElementById('person-department').disabled=!teacher;document.getElementById('person-department').required=teacher;document.getElementById('person-graduation').disabled=teacher;}
  function open(person=null) {
    active=person;document.getElementById('person-form').reset();
    document.getElementById('person-title').textContent=person?'编辑 '+person.username:'新增师生（系统自动分配编号）';
    for(const [id,key] of [['person-name','full_name'],['person-role','role'],['person-birth','birth_date'],['person-ssn','social_security_number'],['person-status','status'],['person-department','department'],['person-graduation','graduation_date']]) if(person) set(id,person[key]);
    document.getElementById('person-role').disabled=!!person;roleFields();modal.classList.add('show');
  }
  document.getElementById('create-person').addEventListener('click',()=>open());
  document.getElementById('cancel-person').addEventListener('click',()=>modal.classList.remove('show'));
  document.getElementById('person-role').addEventListener('change',roleFields);
  document.getElementById('user-search').addEventListener('input',render);
  document.getElementById('person-form').addEventListener('submit',async event=>{
    event.preventDefault();const teacher=value('person-role')==='teacher';
    const profile={full_name:value('person-name').trim(),birth_date:value('person-birth')||null,social_security_number:value('person-ssn').trim()||null,status:value('person-status'),graduation_date:teacher?null:value('person-graduation')||null};
    if(teacher) profile.department=value('person-department').trim();
    if(!active) profile.role=value('person-role');
    const button=event.submitter;button.disabled=true;
    try{const saved=await SECD.request('/api/v1/admin/users'+(active?'/'+active.id:''),{method:active?'PATCH':'POST',body:JSON.stringify(profile)});SECD.showToast(active?'资料已更新':'账号已创建：'+saved.login_number);modal.classList.remove('show');await load();}catch(error){}finally{button.disabled=false;}
  });
  document.getElementById('user-list-body').addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    const key=Object.keys(button.dataset)[0],person=users.find(x=>x.id===Number(button.dataset[key]));if(!person)return;
    if(key==='edit'){open(person);return;}
    if(!await SECD.confirmAction('确认对 '+person.username+' 执行'+({reset:'重置密码',toggle:person.is_active?'停用':'启用',delete:'删除（有历史时停用）'}[key])+'？'))return;
    button.disabled=true;
    try{const suffix={reset:'/reset-password',toggle:'/status',delete:''}[key];const options={method:{reset:'POST',toggle:'PATCH',delete:'DELETE'}[key]};if(key==='toggle')options.body=JSON.stringify({is_active:!person.is_active});const result=await SECD.request('/api/v1/admin/users/'+person.id+suffix,options);SECD.showToast(result.message||'操作完成');await load();}catch(error){button.disabled=false;}
  });
  document.addEventListener('DOMContentLoaded',load);
})();
