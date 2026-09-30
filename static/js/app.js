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
    const headers = new Headers(options.headers || {});
    const token = sessionStorage.getItem('access_token');
    if (token) headers.set('Authorization', `Bearer ${token}`);
    const response = await fetch(path, { credentials: 'omit', ...options, headers });
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* non-JSON response */ }
    if (response.status === 401) {
      sessionStorage.removeItem('access_token');
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
      const response = await fetch(`/api/materials/${encodeURIComponent(materialId)}/download`, {
        credentials: 'omit',
        headers: { Authorization: `Bearer ${sessionStorage.getItem('access_token') || ''}` },
      });
      if (response.status === 401) {
        sessionStorage.removeItem('access_token');
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
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('[data-submit-button]');
      const errorMessage = form.querySelector('[data-login-error]');
      if (button) { button.disabled = true; button.textContent = '正在进入…'; }
      if (errorMessage) { errorMessage.hidden = true; errorMessage.textContent = ''; }
      try {
        const response = await fetch('/api/auth/login', {
          method: 'POST',
          credentials: 'omit',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(Object.fromEntries(new FormData(form))),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || '登录失败，请检查账号和密码');
        sessionStorage.setItem('access_token', payload.access_token);
        window.location.href = '/materials';
      } catch (error) {
        if (errorMessage) {
          errorMessage.textContent = error.message || '无法连接服务，请稍后重试';
          errorMessage.hidden = false;
        }
      } finally {
        if (button) { button.disabled = false; button.textContent = '进入课堂'; }
      }
    });
  };

  const setupDashboard = async () => {
    if (!dashboard) return;
    if (!sessionStorage.getItem('access_token')) {
      window.location.replace('/login');
      return;
    }
    try {
      state.user = (await api('/api/me')).user;
    } catch (error) {
      if (error.message !== '登录状态已过期') showNotice(error.message, 'error');
      return;
    }
    dashboard.hidden = false;
    dashboard.dataset.role = state.user.role;
    document.querySelector('[data-user-name]').textContent = state.user.username;
    document.querySelector('[data-user-avatar]').textContent = state.user.username.slice(0, 1).toUpperCase();
    document.querySelector('[data-user-role]').textContent = state.user.role === 'teacher' ? '教师' : '学生';
    document.querySelector('[data-class-name]').textContent = state.user.class_name;
    document.querySelector('[data-class-heading]').textContent = state.user.class_name;
    document.querySelector('[data-teacher-only]').hidden = state.user.role !== 'teacher';
    document.querySelector('[data-student-only]').hidden = state.user.role !== 'student';
    document.querySelector('[data-student-only]').style.display = state.user.role === 'student' ? 'flex' : 'none';
    await loadMaterials();

    document.addEventListener('click', (event) => {
      if (event.target.closest('[data-refresh]')) loadMaterials();
      const downloadButton = event.target.closest('[data-download-id]');
      if (downloadButton) downloadMaterial(downloadButton.dataset.downloadId, downloadButton);
      if (event.target.closest('[data-logout]')) logout();
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

  const logout = async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' });
    } catch (_) {
      // Clear the browser credential even if revocation cannot be reached.
    } finally {
      sessionStorage.removeItem('access_token');
      window.location.href = '/login';
    }
  };

  setupLoginForm();
  setupDashboard();
})();
