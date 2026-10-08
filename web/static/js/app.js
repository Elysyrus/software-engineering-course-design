const SECD = (() => {
  let pollingTimers = [];

  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const loadSemesters = async id => {
    const data=await request('/api/v1/semesters');
    const select=document.getElementById(id);
    if(!data.semesters.length) throw new Error('暂无学期，请先初始化数据');
    data.semesters.forEach(term=>{const option=document.createElement('option');option.value=term.semester_id;option.textContent=term.code+' ('+(term.status==='open'?'开放':'已关闭')+')';select.appendChild(option);});
    const requested=Number(new URLSearchParams(location.search).get('semester_id'));
    const active=data.semesters.find(x=>x.semester_id===requested) || data.semesters[0];
    select.value=active.semester_id;
    select.addEventListener('change',()=>{const link=new URL(location.href);link.searchParams.set('semester_id',select.value);location.href=link.href;});
    document.querySelectorAll('.sidebar a[href^="/student/"],.sidebar a[href^="/admin/close"],.sidebar a[href^="/admin/closing"]').forEach(link=>{link.href=link.getAttribute('href')+'?semester_id='+active.semester_id;});
    return active;
  };

  const getHeaders = () => {
    const headers = { "Content-Type": "application/json" };
    const csrfToken = document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrf_token="))
      ?.split("=")[1];
    if (csrfToken) headers["X-CSRF-Token"] = decodeURIComponent(csrfToken);
    return headers;
  };

  const showToast = (message, type = "success") => {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${escape(message)}</span><button style="border:none;background:none;cursor:pointer;margin-left:8px;" onclick="this.parentElement.remove()">✕</button>`;
    container.appendChild(toast);
    setTimeout(() => { toast.remove(); }, 4000);
  };

  const request = async (url, options = {}) => {
    options.headers = { ...getHeaders(), ...(options.headers || {}) };
    try {
      const response = await fetch(url, options);
      if (response.status === 401) {
        localStorage.clear();
        window.location.href = "/login";
        throw new Error("登录已失效，请重新登录");
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        let errorMsg=data.detail || data.message || `请求失败 (${response.status})`;
        if(Array.isArray(errorMsg)) errorMsg=errorMsg.map(x=>x.msg).join('；');
        else if(typeof errorMsg==='object') errorMsg=errorMsg.message || JSON.stringify(errorMsg);
        if(response.status===409 && String(errorMsg).includes('重新载入')) triggerConflictBanner();
        throw new Error(errorMsg);
      }
      return data;
    } catch (err) {
      showToast(err.message, "danger");
      err.reported = true;
      throw err;
    }
  };

  const triggerConflictBanner = () => {
    const banner = document.getElementById("conflict-banner");
    if (banner) banner.style.display = "flex";
  };

  const confirmAction = (message) => {
    return new Promise((resolve) => {
      const modal = document.getElementById("confirm-modal");
      const msgElem = document.getElementById("confirm-modal-msg");
      const okBtn = document.getElementById("confirm-modal-ok");
      const cancelBtn = document.getElementById("confirm-modal-cancel");

      msgElem.innerText = message;
      modal.classList.add("show");

      const cleanup = () => {
        modal.classList.remove("show");
        okBtn.onclick = null;
        cancelBtn.onclick = null;
      };

      okBtn.onclick = () => { cleanup(); resolve(true); };
      cancelBtn.onclick = () => { cleanup(); resolve(false); };
    });
  };

  const startPolling = (fetchFn, intervalMs = 6000) => {
    fetchFn();
    const timer = setInterval(fetchFn, intervalMs);
    pollingTimers.push(timer);
    return timer;
  };

  const clearAllPolling = () => {
    pollingTimers.forEach(clearInterval);
    pollingTimers = [];
  };

  window.addEventListener("beforeunload", clearAllPolling);

  return { escape, loadSemesters, request, showToast, confirmAction, startPolling, clearAllPolling, triggerConflictBanner };
})();
