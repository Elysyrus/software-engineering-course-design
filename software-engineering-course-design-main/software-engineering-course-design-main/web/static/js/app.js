const SECD = (() => {
  let pollingTimers = [];

  const getHeaders = () => {
    const token = localStorage.getItem("token");
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
  };

  const showToast = (message, type = "success") => {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${message}</span><button style="border:none;background:none;cursor:pointer;margin-left:8px;" onclick="this.parentElement.remove()">✕</button>`;
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
        return;
      }
      if (response.status === 409) {
        triggerConflictBanner();
        throw new Error("数据版本已过期或已被他人更改，请刷新页面重新获取最新数据。");
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const errorMsg = data.detail || data.message || `请求失败 (${response.status})`;
        throw new Error(errorMsg);
      }
      return data;
    } catch (err) {
      showToast(err.message, "danger");
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

  return { request, showToast, confirmAction, startPolling, clearAllPolling, triggerConflictBanner };
})();