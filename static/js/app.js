(() => {
  const state = { user: null, loading: false, knowledgeRequest: 0, tutoringSessionId: null, tutoringBusy: false };
  const dashboard = document.querySelector('[data-dashboard]');

  const viewNames = { materials: '班级材料', knowledge: '知识检索', settings: '辅导配置', chat: '解题引导', integrations: '工具与 MCP' };
  let availableViews = [];

  const openView = (name, { focus = false } = {}) => {
    if (!availableViews.includes(name)) return;
    document.querySelectorAll('[data-view]').forEach(panel => { panel.hidden = panel.dataset.view !== name; });
    document.querySelectorAll('[data-nav]').forEach(button => {
      const active = button.dataset.nav === name;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    document.querySelector('[data-page-title]').textContent = viewNames[name];
    document.title = `${viewNames[name]} | CampusClaw`;
    if (window.location.hash !== `#${name}`) window.history.replaceState(null, '', `#${name}`);
    if (focus) document.querySelector(`[data-view="${name}"]`).focus({ preventScroll: true });
  };

  const setupWorkspace = () => {
    const role = state.user.role;
    document.querySelectorAll('[data-nav]').forEach(button => {
      button.hidden = Boolean(button.dataset.navRole && button.dataset.navRole !== role);
      if (!button.hidden) availableViews.push(button.dataset.nav);
      button.addEventListener('click', () => openView(button.dataset.nav, { focus: true }));
    });
    document.querySelector('[data-space-label]').textContent = role === 'teacher' ? '教学工作空间' : '我的学习空间';
    document.querySelector('[data-workspace-label]').textContent = role === 'teacher' ? '教学工作台' : '学习空间';
    const history = document.querySelector('[data-history]');
    if (history) history.hidden = role !== 'student';
    const defaultView = role === 'student' && availableViews.includes('chat') ? 'chat' : 'materials';
    const navigate = () => openView(availableViews.includes(window.location.hash.slice(1)) ? window.location.hash.slice(1) : defaultView);
    window.addEventListener('hashchange', navigate);
    navigate();
  };

  const updateChatWelcome = () => {
    const welcome = document.querySelector('[data-chat-welcome]');
    if (!welcome) return;
    welcome.hidden = Boolean(document.querySelector('[data-session-messages]').children.length);
    welcome.querySelector('.welcome-hint').textContent = state.tutoringSessionId
      ? '在下方输入问题，开始这一次探索' : '点击「新建会话」，开始这一次探索';
  };

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
      clearKnowledge();
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
        <div><strong>${escapeHtml(material.title)}</strong><small>${escapeHtml(material.filename)}</small><small>${escapeHtml(({ pending: '正文待索引', indexed: '可检索', failed: '索引失败' })[material.search_index_status] || '正文待索引')}${material.search_error ? ' · ' + escapeHtml(material.search_error) : ''}</small></div>
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

  const clearKnowledge = () => {
    state.knowledgeRequest += 1;
    const results = document.querySelector('[data-knowledge-results]');
    const status = document.querySelector('[data-knowledge-status]');
    if (results) results.replaceChildren();
    if (status) status.textContent = '';
    const messages = document.querySelector('[data-session-messages]');
    const sessions = document.querySelector('[data-session-list]');
    const questionForm = document.querySelector('[data-tutoring-question-form]');
    const teacherPrompt = document.querySelector('[data-tutoring-prompt-form] [name="teacher_prompt"]');
    const skillEditors = document.querySelector('[data-tutoring-skills]');
    if (messages) messages.replaceChildren();
    if (sessions) sessions.replaceChildren();
    if (questionForm) questionForm.hidden = true;
    if (teacherPrompt) teacherPrompt.value = '';
    if (skillEditors) skillEditors.replaceChildren();
    state.tutoringSessionId = null;
  };

  const locationText = (location = {}) => [
    location.page ? `第 ${location.page} 页` : '',
    (location.headings || []).join(' / '),
    location.paragraph ? `第 ${location.paragraph} 段` : '',
    `片段 ${location.chunk || ''}`,
  ].filter(Boolean).join(' · ');

  const renderSource = (item, requestId) => {
    const article = document.createElement('article');
    article.className = 'knowledge-source';
    const title = document.createElement('strong');
    title.textContent = `${item.number ? '[' + item.number + '] ' : ''}${item.title}`;
    const location = document.createElement('small');
    location.textContent = locationText(item.location);
    const quote = document.createElement('p');
    quote.className = 'knowledge-quote';
    quote.textContent = item.excerpt;
    const open = document.createElement('button');
    open.type = 'button'; open.className = 'button button-small button-ghost';
    open.textContent = '查看引用原文';
    open.addEventListener('click', async () => {
      open.disabled = true;
      try {
        const passage = await api(`/api/knowledge/chunks/${encodeURIComponent(item.chunk_id)}`);
        if (requestId !== state.knowledgeRequest) return;
        quote.textContent = passage.excerpt;
        open.textContent = '已显示完整原文';
      } catch (error) {
        if (requestId !== state.knowledgeRequest) return;
        open.textContent = '重新查看原文';
        showNotice(error.message === 'not found' ? '引用已失效或不可访问，请重新检索。' : error.message, 'error');
      } finally { open.disabled = false; }
    });
    const download = document.createElement('button');
    download.type = 'button'; download.className = 'button button-small button-ghost';
    download.textContent = '下载原文件'; download.dataset.downloadId = item.material_id;
    article.append(title, location, quote, open, download);
    return article;
  };

  const setupKnowledge = () => {
    const form = document.querySelector('[data-knowledge-form]');
    if (!form) return;
    const status = document.querySelector('[data-knowledge-status]');
    const results = document.querySelector('[data-knowledge-results]');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const query = form.elements.query.value.trim();
      if (!query) { status.textContent = '请输入关键词或问题。'; return; }
      const asking = event.submitter?.value === 'ask';
      const requestId = ++state.knowledgeRequest;
      results.replaceChildren();
      status.textContent = asking ? '正在根据本班资料回答…' : '正在检索本班资料…';
      const payload = { [asking ? 'question' : 'query']: query, mode: form.elements.mode.value };
      try {
        const response = await api(asking ? '/api/knowledge/ask' : '/api/search', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        if (requestId !== state.knowledgeRequest) return;
        if (asking) {
          if (response.status === 'insufficient_evidence') {
            status.textContent = '本班知识库暂无足够依据'; return;
          }
          const answer = document.createElement('p');
          answer.className = 'knowledge-answer'; answer.textContent = response.answer;
          results.append(answer);
        }
        const items = asking ? response.citations : response.results;
        items.forEach((item) => results.append(renderSource(item, requestId)));
        status.textContent = items.length ? (asking ? '回答与引用来源' : `找到 ${items.length} 条相关片段`) : '本班暂无匹配内容。';
      } catch (error) {
        if (requestId === state.knowledgeRequest) status.textContent = error.message;
      }
    });
  };

  const renderSkillEditor = (skill = null) => {
    const article = document.createElement('article');
    article.className = 'skill-editor';
    article.dataset.skillId = skill ? skill.id : '';
    article.innerHTML = `
      <form data-skill-form class="stack-form">
        <div class="field"><label>技能名称</label><input name="name" maxlength="80" required></div>
        <div class="field"><label>技能类型</label><select name="kind"><option value="solution_guidance">解题引导</option><option value="other">其他</option></select></div>
        <div class="field"><label>指导内容</label><textarea name="instructions" maxlength="2000" rows="3" required></textarea></div>
        <label class="skill-enabled"><input name="enabled" type="checkbox"> 启用技能</label>
        <div class="knowledge-actions"><button class="button button-small button-primary" type="submit">保存技能</button><button class="button button-small button-ghost" type="button" data-delete-skill>删除</button><span data-skill-status role="status"></span></div>
      </form>`;
    if (skill) {
      const form = article.querySelector('form');
      form.elements.name.value = skill.name;
      form.elements.kind.value = skill.kind;
      form.elements.instructions.value = skill.instructions;
      form.elements.enabled.checked = Boolean(skill.enabled);
    }
    return article;
  };

  const setupTeacherTutoring = async () => {
    const panel = document.querySelector('[data-tutoring-teacher]');
    const promptForm = panel.querySelector('[data-tutoring-prompt-form]');
    const skills = panel.querySelector('[data-tutoring-skills]');
    const status = panel.querySelector('[data-tutoring-config-status]');
    const load = async () => {
      const config = await api('/api/tutoring/config');
      promptForm.elements.teacher_prompt.value = config.teacher_prompt;
      skills.replaceChildren(...config.skills.map(renderSkillEditor));
    };
    try { await load(); } catch (error) { status.textContent = error.message; }
    promptForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      status.textContent = '正在保存…';
      try {
        await api('/api/tutoring/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ teacher_prompt: promptForm.elements.teacher_prompt.value }) });
        status.textContent = '提示词已保存。';
      } catch (error) { status.textContent = error.message; }
    });
    panel.querySelector('[data-new-skill]').addEventListener('click', () => {
      const editor = renderSkillEditor();
      editor.querySelector('[name="enabled"]').checked = true;
      skills.append(editor);
      editor.querySelector('[name="name"]').focus();
    });
    skills.addEventListener('submit', async (event) => {
      const form = event.target.closest('[data-skill-form]');
      if (!form) return;
      event.preventDefault();
      const editor = form.closest('[data-skill-id]');
      const skillId = editor.dataset.skillId;
      const payload = { name: form.elements.name.value, kind: form.elements.kind.value,
        instructions: form.elements.instructions.value, enabled: form.elements.enabled.checked };
      const message = form.querySelector('[data-skill-status]');
      try {
        await api(skillId ? `/api/tutoring/skills/${encodeURIComponent(skillId)}` : '/api/tutoring/skills', {
          method: skillId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        await load();
        status.textContent = '技能已保存。';
      } catch (error) { message.textContent = error.message; }
    });
    skills.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-delete-skill]');
      if (!button) return;
      const editor = button.closest('[data-skill-id]');
      if (!editor.dataset.skillId) { editor.remove(); return; }
      const isRequiredGuidance = editor.querySelector('[name="kind"]').value === 'solution_guidance'
        && editor.querySelector('[name="enabled"]').checked;
      const enabledGuidanceCount = [...skills.querySelectorAll('[data-skill-id]')].filter(item =>
        item.querySelector('[name="kind"]').value === 'solution_guidance'
        && item.querySelector('[name="enabled"]').checked).length;
      if (isRequiredGuidance && enabledGuidanceCount <= 1) {
        editor.querySelector('[data-skill-status]').textContent = '至少保留一项启用的解题引导技能。';
        return;
      }
      try {
        await api(`/api/tutoring/skills/${encodeURIComponent(editor.dataset.skillId)}`, { method: 'DELETE' });
        await load(); status.textContent = '技能已删除。';
      } catch (error) { editor.querySelector('[data-skill-status]').textContent = error.message; }
    });
  };

  const appendTutoringCitation = (container, item) => {
    const citation = document.createElement('article');
    citation.className = 'knowledge-source tutoring-citation';
    const heading = document.createElement('strong');
    heading.textContent = `[${item.number}] ${item.title}`;
    const location = document.createElement('small');
    location.textContent = locationText(item.location);
    const quote = document.createElement('p');
    quote.className = 'knowledge-quote'; quote.textContent = item.excerpt;
    const open = document.createElement('button');
    open.type = 'button'; open.className = 'button button-small button-ghost'; open.textContent = '核验引用原文';
    open.addEventListener('click', async () => {
      open.disabled = true;
      try {
        const source = await api(`/api/knowledge/chunks/${encodeURIComponent(item.chunk_id)}`);
        quote.textContent = source.excerpt;
        open.textContent = '已显示当前原文';
      } catch (error) { quote.textContent = error.message === 'not found' ? '引用已失效或不可访问。' : error.message; }
      finally { open.disabled = false; }
    });
    citation.append(heading, location, quote, open);
    container.append(citation);
  };

  const renderTutoringMessages = (messages) => {
    const target = document.querySelector('[data-session-messages]');
    target.replaceChildren();
    messages.forEach((message) => {
      const article = document.createElement('article');
      article.className = `tutoring-message ${message.role === 'user' ? 'from-student' : 'from-guide'}`;
      const label = document.createElement('strong');
      label.textContent = message.role === 'user' ? '你的问题' : '解题引导';
      const body = document.createElement('p');
      body.textContent = message.content;
      article.append(label, body);
      if (message.status === 'failed') {
        const error = document.createElement('small'); error.textContent = '本次处理未完成，可重新提问。'; article.append(error);
      } else if (message.status === 'pending') {
        const pending = document.createElement('small'); pending.textContent = '正在检索与核验…'; article.append(pending);
      }
      if (message.role === 'assistant') (message.citations || []).forEach((item) => appendTutoringCitation(article, item));
      target.append(article);
    });
    updateChatWelcome();
    target.scrollTop = target.scrollHeight;
  };

  const loadTutoringSession = async (sessionId) => {
    const session = await api(`/api/tutoring/sessions/${encodeURIComponent(sessionId)}`);
    state.tutoringSessionId = sessionId;
    renderTutoringMessages(session.messages);
    document.querySelector('[data-tutoring-question-form]').hidden = false;
    document.querySelectorAll('[data-session-select]').forEach(button => button.classList.toggle('active', button.dataset.sessionSelect === String(sessionId)));
  };

  const loadTutoringSessions = async () => {
    const list = document.querySelector('[data-session-list]');
    const payload = await api('/api/tutoring/sessions');
    list.replaceChildren();
    payload.sessions.forEach((session) => {
      const row = document.createElement('div'); row.className = 'session-item';
      const select = document.createElement('button'); select.type = 'button'; select.dataset.sessionSelect = session.id;
      select.textContent = session.title; select.addEventListener('click', () => { openView('chat'); loadTutoringSession(session.id).catch(error => showTutoringStatus(error.message, true)); });
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'session-delete'; remove.textContent = '删除';
      remove.setAttribute('aria-label', `删除会话 ${session.title}`);
      remove.addEventListener('click', async () => {
        try {
          await api(`/api/tutoring/sessions/${encodeURIComponent(session.id)}`, { method: 'DELETE' });
          if (String(state.tutoringSessionId) === String(session.id)) {
            state.tutoringSessionId = null;
            document.querySelector('[data-session-messages]').replaceChildren();
            document.querySelector('[data-tutoring-question-form]').hidden = true;
          }
          await loadTutoringSessions();
        } catch (error) { showTutoringStatus(error.message, true); }
      });
      row.append(select, remove); list.append(row);
    });
    if (state.tutoringSessionId && payload.sessions.some(item => String(item.id) === String(state.tutoringSessionId))) {
      await loadTutoringSession(state.tutoringSessionId);
    } else if (payload.sessions.length) await loadTutoringSession(payload.sessions[0].id);
    updateChatWelcome();
  };

  const showTutoringStatus = (message, isError = false) => {
    const status = document.querySelector('[data-tutoring-status]');
    status.textContent = message;
    status.classList.toggle('error', isError);
  };

  const streamTutoringQuestion = async (question, mode) => {
    const token = sessionStorage.getItem('access_token') || '';
    const response = await fetch(`/api/tutoring/sessions/${encodeURIComponent(state.tutoringSessionId)}/messages`, {
      method: 'POST', credentials: 'omit', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, mode }),
    });
    if (response.status === 401) {
      clearKnowledge(); sessionStorage.removeItem('access_token'); window.location.href = '/login'; throw new Error('登录状态已过期');
    }
    if (!response.ok) {
      let error = {}; try { error = await response.json(); } catch (_) { /* empty error body */ }
      throw new Error(error.error || '辅导请求失败，请稍后重试');
    }
    if ((response.headers.get('Content-Type') || '').includes('application/json')) {
      const result = await response.json();
      if (result.status === 'insufficient_evidence') showTutoringStatus('本班知识库暂无足够依据，可换个问法或询问教师。');
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '', currentEvent = '', completed = false;
    const partial = document.createElement('article');
    partial.className = 'tutoring-message from-guide';
    const heading = document.createElement('strong'); heading.textContent = '解题引导（已核验）';
    const body = document.createElement('p'); body.textContent = '';
    partial.append(heading, body); document.querySelector('[data-session-messages]').append(partial);
    showTutoringStatus('回答已核验，正在发送…');
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const eventLine = block.split('\n').find(line => line.startsWith('event: '));
        const dataLine = block.split('\n').find(line => line.startsWith('data: '));
        if (!eventLine || !dataLine) continue;
        currentEvent = eventLine.slice(7);
        const data = JSON.parse(dataLine.slice(6));
        if (currentEvent === 'delta') body.textContent += data.text;
        if (currentEvent === 'done') completed = true;
      }
      if (done) break;
    }
    if (!completed) throw new Error('连接中断，正在恢复已保存的会话…');
    showTutoringStatus('回答已完成。');
  };

  const setupStudentTutoring = async () => {
    const questionForm = document.querySelector('[data-tutoring-question-form]');
    try { await loadTutoringSessions(); } catch (error) { showTutoringStatus(error.message, true); }
    document.querySelector('[data-create-session]').addEventListener('click', async () => {
      try {
        const created = await api('/api/tutoring/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        await loadTutoringSessions();
        await loadTutoringSession(created.session.id);
        showTutoringStatus('新会话已创建。');
      } catch (error) { showTutoringStatus(error.message, true); }
    });
    questionForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!state.tutoringSessionId || state.tutoringBusy) return;
      const question = questionForm.elements.question.value.trim();
      if (!question) return;
      state.tutoringBusy = true;
      const button = questionForm.querySelector('[data-send-question]'); button.disabled = true;
      showTutoringStatus('正在检索本班知识库并核验回答…');
      try {
        await streamTutoringQuestion(question, questionForm.elements.mode.value);
        questionForm.reset();
      } catch (error) {
        showTutoringStatus(error.message, true);
      } finally {
        try { await loadTutoringSessions(); } catch (_) { /* preserve the visible request status */ }
        state.tutoringBusy = false; button.disabled = false;
      }
    });
  };

  const setupTutoring = () => {
    if (!document.querySelector('[data-tutoring-student]')) return;
    return state.user.role === 'teacher' ? setupTeacherTutoring() : setupStudentTutoring();
  };

  const setupIntegrations = () => {
    const form = document.querySelector('[data-integrations-form]');
    if (!form || state.user.role !== 'teacher') return;
    const list = document.querySelector('[data-tools-list]');
    const status = document.querySelector('[data-integrations-status]');
    const save = document.querySelector('[data-save-tools]');
    const refresh = document.querySelector('[data-refresh-tools]');
    const load = async (force = false) => {
      save.disabled = true; refresh.disabled = true;
      status.textContent = '正在读取工具连接…';
      try {
        const payload = await api('/api/integrations' + (force ? '?refresh=1' : ''));
        const tools = payload.tools || [];
        const available = new Set(tools.map(tool => tool.id));
        const offline = (payload.enabled_tools || []).filter(id => !available.has(id));
        list.replaceChildren();
        [...tools, ...offline.map(id => ({ id, name: id, source: '暂不可用', enabled: true,
          description: '连接恢复前不会调用此工具，可取消勾选后停用。' }))].forEach(tool => {
          const label = document.createElement('label'); label.className = 'tool-card';
          const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
          checkbox.value = tool.id; checkbox.checked = Boolean(tool.enabled);
          const body = document.createElement('span');
          const title = document.createElement('strong'); title.textContent = tool.name;
          const source = document.createElement('small'); source.textContent = tool.source;
          const description = document.createElement('p'); description.textContent = tool.description;
          body.append(title, source, description); label.append(checkbox, body); list.append(label);
        });
        const endpoint = document.querySelector('[data-mcp-endpoint]');
        endpoint.value = payload.mcp_endpoint ? new URL(payload.mcp_endpoint, window.location.origin).href : 'MCP 服务未启用';
        const issues = (payload.errors || []).map(error => error.message);
        if (!payload.model_support) issues.push('当前未配置远程问答模型；工具自动调用需要支持 tool calling 的模型网关。');
        status.textContent = issues.length ? issues.join('；') : '连接已就绪。勾选的工具仅对本班启用。';
        save.disabled = false;
      } catch (error) { status.textContent = error.message; }
      finally { refresh.disabled = false; }
    };
    refresh.addEventListener('click', () => load(true));
    form.addEventListener('submit', async event => {
      event.preventDefault(); save.disabled = true;
      try {
        const enabled = [...list.querySelectorAll('input:checked')].map(input => input.value);
        await api('/api/integrations', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled_tools: enabled }) });
        status.textContent = '本班工具已保存，后续提问将使用新的配置。';
      } catch (error) { status.textContent = error.message; }
      finally { save.disabled = false; }
    });
    load();
  };

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
    setupWorkspace();
    setupIntegrations();
    setupKnowledge();
    await setupTutoring();
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
    clearKnowledge();
    try {
      await api('/api/auth/logout', { method: 'POST' });
    } catch (_) {
      // Clear the browser credential even if revocation cannot be reached.
    } finally {
      clearKnowledge();
      sessionStorage.removeItem('access_token');
      window.location.href = '/login';
    }
  };

  setupLoginForm();
  setupDashboard();
})();
