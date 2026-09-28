(() => {
  const state = { user: null, loading: false };
  const dashboard = document.querySelector('[data-dashboard]');

  const showNotice = (message, kind = 'success') => {
    const notice = document.querySelector('[data-notice]');
    if (!notice) return;
    notice.hidden = !message;
    notice.textContent = message || '';
    notice.classList.toggle('error', kind === 'error');
  };

  const api = async (path, options = {}) => {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* non-JSON response */ }
    if (response.status === 401) {
      window.location.href = '/login';
      throw new Error('登录状态已过期');
    }
    if (response.status === 403) throw new Error(payload.error || '你没有权限执行此操作');
    if (!response.ok) throw new Error(payload.error || '请求失败，请稍后重试');
    return payload;
  };

  const renderMaterials = (materials) => {
    const list = document.querySelector('[data-material-list]');
    const count = document.querySelector('[data-material-count]');
    if (!list) return;
    if (count) count.textContent = `${materials.length} 份材料`;
    if (!materials.length) {
      list.innerHTML = '<div class="empty-state">本班还没有材料，新的内容会显示在这里。</div>';
      return;
    }
    list.innerHTML = materials.map((material) => `
      <article class="material-item">
        <div><strong>${escapeHtml(material.title)}</strong><small>${escapeHtml(material.filename)}</small></div>
        <button class="button button-small button-ghost" type="button" data-download-id="${material.id}">下载文件</button>
      </article>
    `).join('');
  };

  const renderError = (message) => {
    const list = document.querySelector('[data-material-list]');
    if (list) list.innerHTML = `<div class="error-state" role="alert">${escapeHtml(message)} <button class="button button-small button-ghost" type="button" data-refresh>重试</button></div>`;
  };

  const loadMaterials = async () => {
    const list = document.querySelector('[data-material-list]');
    if (!list) return;
    list.innerHTML = '<div class="loading-state"><span class="spinner"></span>正在加载本班材料</div>';
    try {
      const payload = await api('/api/materials');
      renderMaterials(payload.materials || []);
    } catch (error) {
      if (error.message !== '登录状态已过期') renderError(error.message);
    }
  };

  const downloadMaterial = async (materialId, button) => {
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = '准备下载…';
    try {
      const response = await fetch(`/api/materials/${encodeURIComponent(materialId)}/download`, { credentials: 'same-origin' });
      if (response.status === 401) {
        window.location.href = '/login';
        return;
      }
      if (response.status === 403) throw new Error('你没有权限下载这份材料');
      if (response.status === 404) throw new Error('文件暂时不可用');
      if (!response.ok) throw new Error('下载失败，请稍后重试');
      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition') || '';
      const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const plainName = disposition.match(/filename="?([^";]+)"?/i);
      const filename = encodedName ? decodeURIComponent(encodedName[1]) : (plainName ? plainName[1] : 'material');
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      showNotice(error.message, 'error');
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  };

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character]));

  const setupLoginForm = () => {
    const form = document.querySelector('[data-login-form]');
    if (!form) return;
    form.addEventListener('submit', () => {
      const button = form.querySelector('[data-submit-button]');
      if (button) { button.disabled = true; button.textContent = '正在进入…'; }
    });
  };

  const setupDashboard = async () => {
    if (!dashboard) return;
    try {
      state.user = (await api('/api/me')).user;
    } catch (error) {
      if (error.message !== '登录状态已过期') showNotice(error.message, 'error');
      return;
    }
    await loadMaterials();

    document.addEventListener('click', (event) => {
      if (event.target.closest('[data-refresh]')) loadMaterials();
      const downloadButton = event.target.closest('[data-download-id]');
      if (downloadButton) downloadMaterial(downloadButton.dataset.downloadId, downloadButton);
    });

    const form = document.querySelector('[data-upload-form]');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (state.loading) return;
      const button = form.querySelector('[data-upload-button]');
      state.loading = true;
      button.disabled = true;
      button.textContent = '正在上传…';
      showNotice('材料正在写入本班知识库…');
      try {
        await api('/api/materials', { method: 'POST', body: new FormData(form) });
        form.reset();
        showNotice('上传成功，材料已出现在本班列表。');
        await loadMaterials();
      } catch (error) {
        showNotice(error.message, 'error');
      } finally {
        state.loading = false;
        button.disabled = false;
        button.textContent = '上传到知识库';
      }
    });
  };

  setupLoginForm();
  setupDashboard();
})();
