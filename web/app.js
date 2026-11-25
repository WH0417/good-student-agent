/* Local-first revision workspace. Course content is always rendered as data. */
(() => {
  'use strict';

  const $ = (query, root = document) => root.querySelector(query);
  const list = value => Array.isArray(value) ? value : value && typeof value === 'object'
    ? Object.entries(value).map(([id, item]) => typeof item === 'object' && item !== null ? { id, ...item } : { id, title: String(item) }) : [];
  const plain = value => value == null ? '' : typeof value === 'string' ? value : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  const stored = (key, fallback = '') => { try { return localStorage.getItem(`goodstudent_${key}`) || fallback; } catch { return fallback; } };
  const remember = (key, value) => { try { localStorage.setItem(`goodstudent_${key}`, value); } catch { /* private browser */ } };
  const icons = {
    overview: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
    materials: ['M3 7h7l2 2h9v11H3z', 'M3 7V4h7l2 3'],
    map: ['M12 3v6', 'M5 15v-4h14v4', 'M2 16h6v5H2z', 'M9 3h6v5H9z', 'M16 16h6v5h-6z', 'M12 11v9'],
    focus: ['m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2-5.5-2.9-5.5 2.9 1-6.2L3 9.6l6.2-.9z'],
    chapters: ['M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3z', 'M12 6v16', 'M6 9h3', 'M15 9h3', 'M6 13h3', 'M15 13h3'],
    practice: ['M8 3h9l4 4v14H3V3h2', 'M15 3v5h6', 'm7 13 2 2 4-5', 'M7 19h10'],
    records: ['M3 11a9 9 0 1 1 2.5 7', 'M3 4v7h7', 'M12 7v5l3 2'],
    search: ['M17 17l4 4', 'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14'],
    arrow: ['M4 12h16', 'm14 6 6 6-6 6'],
    download: ['M12 3v12', 'm7 10 5 5 5-5', 'M4 17v4h16v-4'],
    check: ['m5 12 4 4L19 6'],
    clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18', 'M12 7v5l3 2'],
    plus: ['M12 5v14', 'M5 12h14'],
    book: ['M3 5c3-1 6-1 9 2 3-3 6-3 9-2v15c-3-1-6-1-9 2-3-3-6-3-9-2z', 'M12 7v15'],
    files: ['M8 3h9l4 4v13H8z', 'M17 3v5h4', 'M4 7H2v15h12'],
  };
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    for (const d of icons[name] || icons.book) { const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', d); svg.append(path); }
    return svg;
  }
  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value == null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = plain(value);
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
      else if (key === 'value') node.value = value;
      else if (key === 'checked' || key === 'disabled' || key === 'hidden' || key === 'open') node[key] = Boolean(value);
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(plain(child)));
    return node;
  }
  const button = (text, action, style = 'secondary', iconName) => el('button', { type: 'button', class: `button ${style}`, onclick: action }, iconName && icon(iconName), text);
  const badge = (text, color = '') => el('span', { class: `badge ${color}`, text });
  const field = (label, input) => el('label', { class: 'field-label' }, label, input);
  const dateLabel = value => {
    if (!value) return '尚未记录';
    const date = new Date(value); return Number.isNaN(date.valueOf()) ? plain(value) : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  };
  function toast(message, error = false) {
    const node = $('#toast'); node.textContent = plain(message); node.className = `toast${error ? ' error' : ''}`; node.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { node.hidden = true; }, error ? 6500 : 4000);
  }
  function report(error) { toast(error.message || plain(error), true); }
  async function busy(node, action) {
    if (node && node.disabled) return;
    if (node) node.disabled = true;
    try { return await action(); } catch (error) { report(error); } finally { if (node && node.isConnected) node.disabled = false; }
  }

  // A deliberately small Markdown renderer. No source HTML is interpreted.
  function inline(target, text) {
    const source = plain(text); const regex = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\[[^\]\n]+\]\([^\s)]+\))/g;
    let index = 0; let match;
    while ((match = regex.exec(source))) {
      if (match.index > index) target.append(document.createTextNode(source.slice(index, match.index)));
      const token = match[0];
      if (token.startsWith('`')) target.append(el('code', { text: token.slice(1, -1) }));
      else if (token.startsWith('**') || token.startsWith('__')) target.append(el('strong', { text: token.slice(2, -2) }));
      else {
        const parts = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
        let safe = false; try { const uri = new URL(parts[2], location.origin); safe = /^https?:$/.test(uri.protocol); } catch { /* show the label */ }
        target.append(safe ? el('a', { href: parts[2], target: '_blank', rel: 'noopener noreferrer', text: parts[1] }) : document.createTextNode(parts[1]));
      }
      index = regex.lastIndex;
    }
    if (index < source.length) target.append(document.createTextNode(source.slice(index)));
    return target;
  }
  function markdown(value) {
    const out = el('div', { class: 'markdown' });
    if (!value) return out;
    if (typeof value === 'object') value = value.text || value.content || plain(value);
    const lines = plain(value).replace(/\r\n?/g, '\n').split('\n');
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (/^\s*```/.test(line)) {
        const language = line.trim().slice(3).trim().toLowerCase(); const code = []; i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        if (i < lines.length) i++;
        if (language === 'mermaid') out.append(mindmap(code.join('\n')));
        else out.append(el('pre', {}, el('code', { text: code.join('\n') })));
        continue;
      }
      const heading = /^\s{0,3}(#{1,6})\s+(.+)$/.exec(line);
      if (heading) { out.append(inline(el(`h${heading[1].length}`), heading[2])); i++; continue; }
      if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) { out.append(el('hr')); i++; continue; }
      if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[i + 1])) {
        const cells = row => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(x => x.trim());
        const table = el('table'); const head = el('tr'); cells(line).forEach(cell => head.append(inline(el('th'), cell))); table.append(el('thead', {}, head));
        const tbody = el('tbody'); i += 2;
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { const tr = el('tr'); cells(lines[i++]).forEach(cell => tr.append(inline(el('td'), cell))); tbody.append(tr); }
        table.append(tbody); out.append(el('div', { class: 'table-scroll' }, table)); continue;
      }
      if (/^\s*>/.test(line)) {
        const quote = []; while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ''));
        out.append(el('blockquote', {}, markdown(quote.join('\n')))); continue;
      }
      if (/^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(line)) {
        const ordered = /^\s*\d/.test(line); const ul = el(ordered ? 'ol' : 'ul');
        while (i < lines.length && /^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(lines[i])) ul.append(inline(el('li'), lines[i++].replace(/^\s*(?:[-*+]\s+|\d+[.)]\s+)/, '')));
        out.append(ul); continue;
      }
      const paragraph = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(?:```|#{1,6}\s|>|[-*+]\s|\d+[.)]\s)/.test(lines[i])) { paragraph.push(lines[i++]); if (i < lines.length && lines[i].includes('|') && i + 1 < lines.length && /[-|]{3}/.test(lines[i + 1])) break; }
      if (!paragraph.length) paragraph.push(lines[i++]);
      const p = el('p'); paragraph.forEach((text, n) => { if (n) p.append(el('br')); inline(p, text); }); out.append(p);
    }
    return out;
  }

  const modules = [
    ['materials', '资料与设置', 'COURSE MATERIALS'], ['overview', '复习总览', 'MY STUDY SPACE'],
    ['map', '知识脉络导图', 'CONNECT THE IDEAS'], ['focus', '重点清单', 'KNOW YOUR PRIORITIES'],
    ['chapters', '章节讲解', 'LEARN CHAPTER BY CHAPTER'], ['practice', '练习与解析', 'PRACTICE & REFLECT'], ['records', '学习记录导出', 'YOUR LEARNING JOURNEY'],
  ];
  const state = {
    token: '', courses: [], defaults: {}, courseId: stored('course'), page: stored('page', 'overview'),
    detail: null, preview: false, chapterId: '', mapChapter: '', focusFilter: 'all',
    practiceOrigin: null, practiceScopes: { provided: { chapter: 'all', mistakesOnly: false, index: 0 }, agent: { chapter: 'all', mistakesOnly: false, index: 0 } }, questionIndex: 0, answers: new Map(), revealed: new Map(), challenges: new Map(), challengeReasons: new Map(), answerRevisions: new Map(),
    files: [], selectedFiles: new Set(), sourceSelection: new Set(), knownSourceIds: new Set(), fileKinds: {}, materialPath: '', request: '', customQuestion: '',
    search: '', searchResults: null, exportResult: null, consentJob: null, consentQueue: [], revokeCourseId: '', refreshing: false, needsRender: false, poll: null, generation: 0, renderGeneration: 0,
  };
  if (!modules.some(x => x[0] === state.page)) state.page = 'overview';
  const hash = new URLSearchParams(location.hash.slice(1));
  try { state.token = hash.get('token') || sessionStorage.getItem('goodstudent_session_token') || ''; if (state.token) sessionStorage.setItem('goodstudent_session_token', state.token); } catch { state.token = hash.get('token') || ''; }
  if (hash.has('token')) history.replaceState(null, '', location.pathname + location.search);

  async function api(path, payload) {
    const response = await fetch(path, { method: payload === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: { 'X-Session-Token': state.token, ...(payload !== undefined ? { 'Content-Type': 'application/json' } : {}) }, ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}) });
    let body; try { body = await response.json(); } catch { throw new Error(`本地服务返回异常（${response.status}），请重新打开工作台。`); }
    if (!response.ok || body.ok === false) throw new Error(plain(body.error && typeof body.error === 'object' ? body.error.message || body.error : body.error) || `请求未完成（${response.status}）`);
    return Object.hasOwn(body, 'data') ? body.data : body;
  }
  const url = (path, params = {}) => { const query = new URLSearchParams({ course_id: state.courseId, ...Object.fromEntries(Object.entries(params).filter(([, value]) => value != null && value !== '')) }); return `${path}?${query}`; };
  const currentCourse = () => state.detail?.course || state.courses.find(c => c.id === state.courseId) || {};
  const currentBundle = () => state.preview ? state.detail?.preview || state.detail?.package : state.detail?.package || state.detail?.preview;
  const isPreview = () => !!currentBundle() && (state.preview || !state.detail?.package);
  const chapters = () => {
    const bundle = currentBundle(); const completed = list(bundle?.chapters); const plans = list(bundle?.manifest?.chapters);
    return (plans.length ? plans.map(plan => ({ ...plan, ...completed.find(c => c.id === plan.id) })) : completed).map((chapter, n) => ({ ...chapter, id: chapter.id || `chapter_${n + 1}`, title: chapter.title || `第 ${n + 1} 章` }));
  };
  const activeChapter = () => chapters().find(c => c.id === state.chapterId) || chapters()[0];
  const sources = () => list(state.detail?.sources);
  const jobs = () => list(state.detail?.jobs).slice().sort((a, b) => plain(b.updated_at || b.created_at || b.at).localeCompare(plain(a.updated_at || a.created_at || a.at)));
  const jobStatus = job => job.authorization_required ? 'awaiting_authorization' : job.status || job.state || 'waiting_agent';
  const activeJobs = () => jobs().filter(job => ['awaiting_authorization', 'waiting_agent', 'queued', 'pending', 'running', 'processing', 'interrupted', 'partial'].includes(jobStatus(job)));
  const progressFor = id => state.detail?.workbench?.reading?.[id] || state.detail?.progress?.[id] || {};
  const hasRead = id => ['read', 'self_assessed', 'practice_passed', 'explained'].includes(progressFor(id).status);
  const modeText = mode => mode === 'pass' ? '速通模式' : mode === 'high' ? '高分模式' : '尚未选择模式';
  const jobLabels = { awaiting_authorization: '等待授权', waiting_agent: '等待 Agent 处理', queued: '等待处理', pending: '等待处理', running: '正在整理', processing: '正在整理', interrupted: '任务已中断 · 可继续', partial: '已有部分章节', complete: '已完成', completed: '已完成', failed: '处理未完成', error: '处理未完成', cancelled: '已停止' };
  const progressLabels = { read: '已读', self_assessed: '自评已理解', explained: '已讲解', needs_review: '待复习', practice_passed: '独立练习通过' };

  function navigate(page, chapterId) {
    state.page = page; state.searchResults = null; state.search = ''; $('#global-search').value = '';
    if (chapterId) { state.chapterId = chapterId; remember(`chapter_${state.courseId}`, chapterId); }
    remember('page', page); render(); $('#main').focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: 'instant' });
  }
  async function chooseCourse(id) {
    if (!id) return;
    state.courseId = id; remember('course', id); state.detail = null; state.chapterId = stored(`chapter_${id}`); state.preview = false;
    restoreSourceSelection(id);
    state.questionIndex = 0; state.practiceOrigin = null; state.practiceScopes = { provided: { chapter: 'all', mistakesOnly: false, index: 0 }, agent: { chapter: 'all', mistakesOnly: false, index: 0 } }; state.answers.clear(); state.revealed.clear(); state.challenges.clear(); state.challengeReasons.clear(); state.answerRevisions.clear(); state.exportResult = null; state.files = []; state.selectedFiles.clear(); state.searchResults = null;
    $('#main').replaceChildren(el('div', { class: 'loading-screen' }, el('div', { class: 'spinner' }), el('p', { text: '正在打开课程…' })));
    await refreshCourse();
  }
  async function refreshCourse({ quiet = false } = {}) {
    if (!state.courseId || quiet && state.refreshing) return;
    state.refreshing = true; const id = state.courseId; const generation = ++state.generation;
    try {
      const detail = await api(url('/api/course'));
      if (id !== state.courseId || generation !== state.generation) return;
      const previousVersions = new Map(allQuestions().map(question => [question.id, question.reference_version]));
      const changed = JSON.stringify(detail) !== JSON.stringify(state.detail); state.detail = detail;
      for (const question of allQuestions()) if (previousVersions.get(question.id) && question.reference_version && previousVersions.get(question.id) !== question.reference_version) {
        state.answerRevisions.set(question.id, { previous: previousVersions.get(question.id), current: question.reference_version, at: new Date().toISOString() });
        state.challenges.delete(question.id);
      }
      syncSourceSelection();
      if (changed) state.needsRender = true;
      if (!state.chapterId) state.chapterId = detail.workbench?.last_chapter || chapters()[0]?.id || '';
      $('#connection-state').textContent = state.detail.runner?.available ? '已连接本地服务 · 自动 Agent 已连接' : '已连接本地服务 · 等待当前对话 Agent 接手';
      if (!quiet || state.needsRender && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) render();
    } catch (error) { if (!quiet) { report(error); render(); } $('#connection-state').textContent = '本地服务暂时未连接'; }
    finally { if (generation === state.generation) { state.refreshing = false; schedulePoll(); } }
  }
  function schedulePoll() {
    clearTimeout(state.poll);
    if (activeJobs().length || state.needsRender) state.poll = setTimeout(() => refreshCourse({ quiet: true }), 3000);
  }
  function heading(title, subtitle, actions = [], eyebrow) {
    return el('div', { class: 'page-heading' }, el('div', {}, el('p', { class: 'eyebrow', text: eyebrow || modules.find(m => m[0] === state.page)?.[2] || 'STUDY WORKSPACE' }), el('h1', { text: title }), subtitle && el('p', { text: subtitle })), el('div', { class: 'page-heading-actions' }, ...actions));
  }
  function section(title, subtitle, action) { return el('div', { class: 'section-heading' }, el('div', {}, el('h2', { text: title }), subtitle && el('p', { text: subtitle })), action); }
  function empty(title, subtitle, action, name = 'book') { return el('div', { class: 'empty-state' }, el('div', { class: 'empty-icon' }, icon(name)), el('h3', { text: title }), el('p', { text: subtitle }), action); }
  function notice(text, type = '') { return el('div', { class: `notice ${type}`, text }); }
  function refs(value) {
    return el('div', { class: 'source-refs' }, ...list(value).map(ref => {
      const source = sources().find(s => s.id === ref.source_id); const locator = ref.locator || {};
      const name = ref.file || ref.filename || ref.source || source?.filename || ref.source_id || '';
      const page = ref.page || ref.page_number || locator.page || locator.slide; const time = ref.timestamp ?? locator.timestamp ?? locator.start;
      const text = [name, page && (locator.slide ? `幻灯片 ${page}` : `第 ${page} 页`), locator.paragraph && `第 ${locator.paragraph} 段`, locator.text_block && `文本块 ${locator.text_block}`, time != null && time !== false && `时间 ${plain(time)}`].filter(Boolean).join(' · ') || ref.text || '课程资料';
      return el('span', { class: 'source-ref', text, title: text });
    }));
  }
  function render() {
    state.renderGeneration++; state.needsRender = false;
    for (const old of document.querySelectorAll('[data-blob-url]')) URL.revokeObjectURL(old.dataset.blobUrl);
    const nav = $('#navigation'); nav.replaceChildren(...modules.map(([id, label]) => el('button', { type: 'button', class: `nav-button${state.page === id && state.searchResults === null ? ' active' : ''}`, 'aria-current': state.page === id && state.searchResults === null ? 'page' : null, onclick: () => navigate(id) }, icon(id), label)));
    const courseSelect = $('#course-select'); courseSelect.replaceChildren(...(state.courses.length ? state.courses.map(c => el('option', { value: c.id, text: c.name || '课程记录异常', disabled: !!c.error })) : [el('option', { value: '', text: '还没有课程' })])); courseSelect.value = state.courseId;
    $('#breadcrumb-course').textContent = currentCourse().name || '我的课程'; $('#global-search').disabled = !state.courseId;
    const main = $('#main');
    if (!state.token) { main.replaceChildren(el('div', { class: 'no-auth card' }, empty('请从本地启动器打开', '当前页面缺少会话凭据。请使用“打开工作台”入口重新打开；学习资料仍保存在本地。', button('重新连接', () => location.reload(), 'primary')))); return; }
    if (!state.courseId || !state.detail) { main.replaceChildren(welcome()); return; }
    const content = state.searchResults !== null ? searchPage() : ({ materials: materialsPage, overview: overviewPage, map: mapPage, focus: focusPage, chapters: chaptersPage, practice: practicePage, records: recordsPage }[state.page] || overviewPage)();
    main.replaceChildren(content);
    const packageMode = currentBundle()?.manifest?.mode; const goalMode = currentCourse().mode;
    if (packageMode && goalMode && packageMode !== goalMode) content.insertBefore(notice(`当前显示${modeText(packageMode)}资料，复习目标已更新为${modeText(goalMode)}。重新整理后生效，原有内容仍可继续阅读。`, 'warning'), content.children[1] || null);
    if (state.searchResults === null && state.page !== 'materials') {
      const active = activeJobs()[0]; if (active) content.insertBefore(jobBanner(active), content.children[1] || null);
    }
  }
  function welcome() {
    return el('div', {}, heading('欢迎来到你的复习工作台', '从一门课开始，把零散资料变成清楚的复习路线。'),
      el('div', { class: 'welcome-intro' }, el('div', { class: 'brand-mark' }, icon('book')), el('h1', { text: '每一门课，都有自己的学习空间' }), el('p', { text: '整理课件与课堂记录，找到重点，逐章理解，再用练习检验收获。复习不必再从一堆文件里重新开始。' }), button('添加我的第一门课程', openCourseDialog, 'primary', 'plus')),
      el('div', { class: 'welcome-steps' }, ...[['01', '导入你的资料', '课件、课堂录音、往年题，放在一起'], ['02', '生成复习内容', '由 Agent 整理知识脉络、重点与讲解'], ['03', '边学边留下记录', '查看解析、记录错题、导出复习包']].map(([num, title, text]) => el('div', { class: 'card' }, el('span', { class: 'welcome-step-index', text: num }), el('div', {}, el('strong', { text: title }), el('p', { text }))))));
  }
  function jobBanner(job) {
    const status = jobStatus(job); const authorization = status === 'awaiting_authorization'; const waiting = ['waiting_agent', 'pending', 'queued'].includes(status); const interrupted = ['interrupted', 'partial'].includes(status);
    return el('div', { class: `notice job-banner${interrupted ? ' warning' : ''}` }, el('div', { class: 'job-icon' }, ['running', 'processing'].includes(status) ? el('div', { class: 'spinner' }) : icon('clock')),
      el('div', { class: 'job-banner-body' }, el('strong', { text: jobLabels[status] || status }), el('p', { text: authorization ? '这项任务需要先由你确认，使用已登录的 Codex（OpenAI）处理本课程选中资料。确认前不会开始处理。' : waiting ? state.detail?.runner?.available ? '任务已进入队列，已连接的自动 Agent 会接单处理。完成更多内容后会估算当前阶段的完成时间。' : '任务已经保存，等待当前对话中的 Agent 接手。已整理的内容仍可继续阅读。' : interrupted ? '已完成的章节和学习记录已保留。继续整理会提交后续任务，不必重新学习已完成的章节。' : plain(job.message || job.progress_message || job.stage || '正在整理课程内容，已完成的章节可先预览。') }), waiting && state.detail?.runner?.message && el('p', { class: 'subtle', text: state.detail.runner.message }), jobProgress(job), jobErrors(job)),
      authorization ? button('允许 Codex 处理', () => openConsentDialog(job), 'primary small') : interrupted && job.kind === 'build' ? button('继续整理', e => busy(e.currentTarget, () => submitBuild(job)), 'secondary small') : button('查看任务', () => navigate('materials'), 'secondary small'));
  }
  function jobProgress(job) {
    const status = jobStatus(job); const waiting = ['waiting_agent', 'pending', 'queued'].includes(status);
    if (status === 'awaiting_authorization') return el('div', { class: 'job-progress' }, el('span', { class: 'job-stage', text: '等待授权 · 尚未开始处理' }));
    const running = ['running', 'processing'].includes(status); const complete = ['complete', 'completed'].includes(status) && !job.error && !job.error_details;
    const progress = job.progress && typeof job.progress === 'object' ? job.progress : {};
    const raw = progress.percent ?? job.percent; const measured = raw !== undefined && raw !== null && raw !== '' && Number.isFinite(Number(raw));
    const percent = waiting ? 0 : complete ? 100 : measured ? Math.max(0, Math.min(100, Number(raw))) : null;
    const estimated = progress.estimated_completion_at || job.estimated_completion_at;
    const rawSeconds = progress.eta_seconds ?? job.eta_seconds; const seconds = rawSeconds !== undefined && rawSeconds !== null && rawSeconds !== '' ? Number(rawSeconds) : NaN;
    let eta = waiting ? '接单后估算' : complete ? '本次任务已完成' : '完成更多内容后估算';
    if (running && estimated) eta = `当前阶段预计完成：${dateLabel(estimated)}`;
    else if (running && Number.isFinite(seconds) && seconds >= 0) eta = seconds < 60 ? '当前阶段预计还需不到 1 分钟' : seconds < 3600 ? `当前阶段预计还需约 ${Math.ceil(seconds / 60)} 分钟` : `当前阶段预计还需约 ${Math.floor(seconds / 3600)} 小时 ${Math.ceil(seconds % 3600 / 60)} 分钟`;
    const label = percent == null ? '完成比例暂未估算' : `${Math.round(percent * 10) / 10}%`;
    return el('div', { class: 'job-progress' }, el('div', { class: 'inline-row space-between' }, el('span', { class: 'job-percent', text: label }), el('span', { class: 'job-eta', text: eta })),
      el('div', { class: 'job-progress-track', role: 'progressbar', 'aria-label': '任务完成进度', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': percent, 'aria-valuetext': label }, el('span', { style: `width:${percent ?? 0}%` })),
      job.stage && el('div', { class: 'job-stage', text: `当前阶段：${job.stage}` }));
  }
  function locatorLabel(locator) {
    if (locator == null || locator === '') return '';
    if (typeof locator !== 'object') return plain(locator);
    return [locator.page && `第 ${locator.page} 页`, locator.slide && `幻灯片 ${locator.slide}`, locator.text_block && `文本块 ${locator.text_block}`, locator.paragraph && `第 ${locator.paragraph} 段`, (locator.timestamp ?? locator.start ?? locator.time_start) != null ? `时间 ${locator.timestamp ?? locator.start ?? locator.time_start}` : '', locator.sheet && `工作表 ${locator.sheet}`].filter(Boolean).join(' · ') || plain(locator);
  }
  function jobErrors(job) {
    const raw = job.error_details; const items = Array.isArray(raw) ? raw : raw ? [raw] : job.error ? [job.error] : [];
    if (!items.length) return null;
    return el('details', { class: 'job-errors', open: ['failed', 'error', 'partial', 'interrupted'].includes(jobStatus(job)) }, el('summary', { text: '查看处理问题与位置' }),
      ...items.map(item => typeof item === 'object' && item !== null ? el('div', { class: 'job-error-item' }, item.stage && el('p', { text: `阶段：${item.stage}` }), item.filename && el('p', { class: 'break-anywhere', text: `文件：${item.filename}` }), item.locator && el('p', { text: `位置：${locatorLabel(item.locator)}` }), el('p', { text: item.message || item.error || item.reason || plain(item) })) : el('p', { class: 'job-error-item', text: plain(item) })));
  }
  function previewRibbon() {
    if (!isPreview()) return null;
    const warnings = currentBundle()?.manifest?.warnings;
    return el('div', {}, el('div', { class: 'preview-ribbon' }, icon('clock'), el('span', { text: '当前显示整理中的预览。已完成章节可直接阅读，尚未完成的内容会明确标出。' }), state.detail.package && button('返回已完成版本', () => { state.preview = false; render(); }, 'secondary small')), Array.isArray(warnings) && warnings.length && notice('尚待完成：' + warnings.map(plain).join('；'), 'warning'));
  }
  function overviewPage() {
    const course = currentCourse(); const items = chapters(); const readCount = items.filter(c => hasRead(c.id)).length; const percent = items.length ? Math.round(readCount / items.length * 100) : 0;
    const active = activeChapter(); const focus = allFocus(); const attempts = list(state.detail.attempts); const hasSources = sources().length > 0;
    const hero = el('div', { class: 'hero' }, el('div', { class: 'hero-copy' }, el('p', { class: 'eyebrow', text: 'YOUR NEXT SMALL STEP' }), el('h2', { text: active ? `从「${active.title}」继续` : hasSources ? '资料已就绪，生成完整复习包' : '把课程资料，变成一条清楚的复习路线' }), el('p', { text: active ? plain(active.summary || '理解核心概念，再用一道练习检查自己的思路。') : hasSources ? '前往资料与设置，选择本轮整理范围后开始生成知识脉络、重点和章节讲解。' : '先添加课件或课堂记录，再让 Agent 整理知识脉络和重点。' }), button(active ? '继续学习' : hasSources ? '选择范围并生成' : '导入课程资料', () => navigate(active ? 'chapters' : 'materials'), 'primary', 'arrow'), el('span', { class: 'hero-pill', text: active ? '  阅读和查看解析无需等待生成' : '  每门课程独立留存' })),
      el('div', { class: 'progress-ring', style: `--progress:${percent}%` }, el('div', {}, el('strong', { text: `${percent}%` }), el('small', { text: '章节阅读进度' }))));
    const stats = el('div', { class: 'stat-grid' }, ...[[sources().length, '课程资料', '份', 'files', ''], [items.length, '复习章节', '章', 'chapters', 'blue'], [focus.length, '重点知识', '项', 'focus', 'orange'], [attempts.length, '累计练习', '次', 'practice', 'purple']].map(([value, label, unit, ico, color]) => el('div', { class: 'stat-card' }, el('div', {}, el('div', { class: 'stat-label', text: label }), el('div', { class: 'stat-value' }, value, el('small', { text: unit }))), el('div', { class: `stat-icon ${color}` }, icon(ico)))));
    const chapterList = el('section', { class: 'card' }, section('我的复习章节', `${readCount} / ${items.length} 章已读或自评理解`, button('全部章节 →', () => navigate('chapters'), 'text')),
      items.length ? el('div', {}, ...items.slice(0, 6).map((chapter, n) => chapterRow(chapter, n))) : empty(hasSources ? '资料已就绪，等待生成复习章节' : '还没有整理好的章节', hasSources ? '前往资料与设置选择本轮范围，生成复习资料后，章节会在这里出现。' : '先导入课程资料并提交整理任务，完成后会在这里出现。', button(hasSources ? '生成复习资料' : '去导入资料', () => navigate('materials'), 'ghost small'), 'chapters'));
    const priorities = el('section', { class: 'card' }, section('这一轮先复习', '每一条重点，都保留它的依据', button('重点清单 →', () => navigate('focus'), 'text')),
      focus.length ? el('div', {}, ...focus.slice(0, 4).map(item => el('div', { class: 'chapter-row' }, el('div', { class: 'chapter-row-body' }, el('div', { class: 'chapter-row-title', text: focusTitle(item) }), el('div', { class: 'chapter-meta', text: focusNote(item) || '查看重点清单中的来源和说明' })), badge(focusTypes[focusType(item)].short, focusType(item))))) : empty('重点会在这里逐渐清楚', '老师强调的内容、你自己指定的重点和学习建议，会分别标记。', null, 'focus'));
    const next = el('section', { class: 'card' }, section('接下来的一小步', null), el('p', { class: 'muted small-text', text: typeof state.detail.next_task === 'object' ? state.detail.next_task.title || state.detail.next_task.description : state.detail.next_task || '导入课程资料，开始本轮复习。' }), el('div', { class: 'divider' }), el('div', { class: 'inline-row space-between' }, el('span', { class: 'subtle', text: `当前目标 · ${modeText(course.mode)}` }), button('调整目标', () => navigate('materials'), 'text')));
    const body = el('div', {}, heading(`${course.name || '我的课程'}，开始今天的复习`, '知识脉络、课堂重点和学习进度，都在这里。', [badge(`复习目标 · ${modeText(course.mode)}`, course.mode || 'teal')]), previewRibbon(), hero, stats,
      el('div', { class: 'grid-two' }, chapterList, el('div', { class: 'stack' }, priorities, next)));
    if (currentBundle()?.overview) body.append(el('section', { class: 'card section-spacer' }, section('课程知识概览'), markdown(currentBundle().overview)));
    return body;
  }
  function chapterRow(chapter, index) {
    const progress = progressFor(chapter.id); const pending = ['pending', 'waiting', 'not_started'].includes(chapter.status);
    return el('div', { class: 'chapter-row' }, el('div', { class: 'chapter-number', text: String(index + 1).padStart(2, '0') }), el('div', { class: 'chapter-row-body' }, el('div', { class: 'chapter-row-title', text: chapter.title }), el('div', { class: 'chapter-meta', text: pending ? '内容尚在整理中' : progressLabels[progress.status] || '等待开始 · 按自己的节奏学习' })), progress.status && badge(progressLabels[progress.status] || progress.status, progress.status === 'needs_review' ? 'orange' : 'teal'), button(pending ? '查看预览' : hasRead(chapter.id) ? '再看一遍' : '开始学习', () => navigate('chapters', chapter.id), 'secondary small'));
  }

  function humanSize(bytes) { if (!Number.isFinite(Number(bytes))) return ''; const n = Number(bytes); return n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n > 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`; }
  function restoreSourceSelection(courseId) {
    let saved; try { saved = JSON.parse(stored(`source_scope_${courseId}`, 'null')); } catch { saved = null; }
    state.sourceSelection = new Set(Array.isArray(saved?.selected) ? saved.selected.filter(x => typeof x === 'string') : []);
    state.knownSourceIds = new Set(Array.isArray(saved?.known) ? saved.known.filter(x => typeof x === 'string') : []);
  }
  function saveSourceSelection() {
    if (!state.courseId) return;
    remember(`source_scope_${state.courseId}`, JSON.stringify({ selected: [...state.sourceSelection], known: [...state.knownSourceIds] }));
  }
  function syncSourceSelection() {
    // Newly discovered sources start selected. An existing deselection survives
    // polling, page reloads and switching back from another course.
    for (const source of sources()) if (source.id && !state.knownSourceIds.has(source.id)) {
      state.knownSourceIds.add(source.id); state.sourceSelection.add(source.id);
    }
    saveSourceSelection();
  }
  function selectedSourceIds() { return sources().filter(source => source.id && state.sourceSelection.has(source.id)).map(source => source.id); }
  function updateScopeSummary() {
    const node = $('#build-scope-summary');
    if (node) node.textContent = `本轮范围：已归档资料 ${selectedSourceIds().length} 份 + 待导入文件 ${state.selectedFiles.size} 份`;
    const count = $('#archive-selection-count');
    if (count) count.textContent = `本轮已选 ${selectedSourceIds().length} / ${sources().length} 份`;
    const all = $('#archive-select-all');
    if (all) { all.checked = sources().length > 0 && selectedSourceIds().length === sources().length; all.indeterminate = selectedSourceIds().length > 0 && selectedSourceIds().length < sources().length; }
  }
  const kindNames = { auto: '自动识别', material: '课件 / 教材', course_focus: '课程重点', exam: '往年试卷', exercise: '练习 / 自测题', audio: '课堂录音', solution: '参考答案', transcript: '课堂逐字记录', summary: '课堂纪要' };
  const selectableKindNames = Object.fromEntries(Object.entries(kindNames).filter(([kind]) => !['solution', 'transcript', 'summary'].includes(kind)));
  function kindFor(file) {
    if (state.fileKinds[file.path]) return state.fileKinds[file.path];
    if (file.kind && selectableKindNames[file.kind]) return file.kind;
    if (/\.(m4a|mp3|wav|ogg|flac|aac|wma|mp4|webm)$/i.test(file.path || file.name)) return 'audio';
    return 'auto';
  }
  function materialsPage() {
    const course = currentCourse(); const path = el('input', { value: state.materialPath, placeholder: '选择课程资料所在的文件夹', 'aria-label': '资料文件夹路径', oninput: e => { state.materialPath = e.target.value; } });
    const fileContainer = el('div', { class: 'card' }, section('添加课程资料', '支持课件、文档、试题、课堂文字记录和录音文件'),
      el('div', { class: 'path-row' }, path, button('选择文件夹', e => busy(e.currentTarget, () => pickFiles('directory')), 'secondary'), button('扫描文件', e => busy(e.currentTarget, () => scanDirectory(path.value)), 'primary')),
      el('div', { class: 'inline-row' }, button('选择文件', e => busy(e.currentTarget, () => pickFiles('files')), 'secondary small', 'files'), button('添加课堂录音', e => busy(e.currentTarget, () => pickFiles('audio')), 'secondary small', 'plus')));
    if (state.files.length) {
      const allCheck = el('input', { type: 'checkbox', checked: state.selectedFiles.size === state.files.length, 'aria-label': '全选资料', onchange: e => { state.files.forEach(f => e.target.checked ? state.selectedFiles.add(f.path) : state.selectedFiles.delete(f.path)); render(); } });
      fileContainer.append(el('div', { class: 'file-toolbar' }, el('label', { class: 'inline-row' }, allCheck, `已选择 ${state.selectedFiles.size} / ${state.files.length} 份`), button('清空选择列表', () => { state.files = []; state.selectedFiles.clear(); render(); }, 'text')),
        el('div', { class: 'file-list' }, ...state.files.map(file => {
          const select = el('select', { 'aria-label': `${file.name} 的资料类型`, onchange: e => { state.fileKinds[file.path] = e.target.value; } }, ...Object.entries(selectableKindNames).map(([value, title]) => el('option', { value, text: title }))); select.value = kindFor(file);
          return el('div', { class: 'file-item' }, el('label', {}, el('input', { type: 'checkbox', checked: state.selectedFiles.has(file.path), onchange: e => { e.target.checked ? state.selectedFiles.add(file.path) : state.selectedFiles.delete(file.path); const line = fileContainer.querySelector('.file-toolbar label'); if (line) line.lastChild.textContent = `已选择 ${state.selectedFiles.size} / ${state.files.length} 份`; updateScopeSummary(); } }), el('span', { class: 'type-icon', text: (file.name || file.path).split('.').pop().slice(0, 4).toUpperCase() }), el('span', {}, el('span', { class: 'file-name', text: file.name || file.path.split(/[\\/]/).pop(), title: file.path }), el('span', { class: 'file-size', text: humanSize(file.size) }))), select);
        })));
    } else fileContainer.append(empty('把这一门课的资料放在一起', '选择文件夹后扫描，或直接添加课件与录音。原始文件路径会保留，任务完成后可从章节中回到来源。', null, 'materials'));
    const request = el('textarea', { placeholder: '可选：告诉 Agent 这次优先整理哪些章节、考试范围或你最困惑的地方。', value: state.request, oninput: e => { state.request = e.target.value; }, 'aria-label': '补充整理要求' });
    fileContainer.append(field('这次整理的补充要求', request), el('p', { id: 'build-scope-summary', class: 'build-scope-summary', text: `本轮范围：已归档资料 ${selectedSourceIds().length} 份 + 待导入文件 ${state.selectedFiles.size} 份` }), el('div', { class: 'inline-row section-spacer' }, button('提交整理任务', e => busy(e.currentTarget, () => submitBuild()), 'primary', 'arrow'), badge(state.detail?.runner?.available ? '自动 Agent 已连接' : '等待当前对话 Agent 接手', state.detail?.runner?.available ? 'teal' : 'outlined')), el('p', { class: 'action-note', text: '勾选下方已归档资料，限定本轮整理范围。取消勾选只排除本轮，不删除资料；“继续整理”沿用该任务原有范围。' }));
    const settings = settingsCard(course);
    const runner = state.detail?.runner || {}; const runnerCard = el('section', { class: 'card' }, section('Agent 处理连接'), el('div', { class: 'inline-row' }, badge(runner.available ? '已连接自动 Agent' : '等待当前对话接手', runner.available ? 'teal' : 'orange')), el('p', { class: 'small-text muted section-spacer', text: runner.message || (runner.available ? '提交任务后，已授权资料可以自动接单处理。' : '自动 Agent 暂未连接，已提交任务会保留，等待当前对话中的 Agent 接手。') }), el('p', { class: 'settings-description', text: '连接状态不代表资料已获授权。仅处理你确认过的本课程资料，新增资料或换课程需要再次确认。' }), el('div', { class: 'section-spacer' }, button('撤销本课程授权', () => openRevokeConsentDialog(), 'text')));
    const sourceCard = el('section', { class: 'card' }, section('已归档资料 · 本轮选择', `${sources().length} 份资料持续留存，勾选需要参加本轮整理的内容`));
    if (sources().length) {
      const all = el('input', { id: 'archive-select-all', type: 'checkbox', checked: selectedSourceIds().length === sources().length, 'aria-label': '本轮全选已归档资料', onchange: e => {
        sources().forEach(source => e.target.checked ? state.sourceSelection.add(source.id) : state.sourceSelection.delete(source.id));
        saveSourceSelection(); sourceCard.querySelectorAll('input[data-source-id]').forEach(input => { input.checked = state.sourceSelection.has(input.dataset.sourceId); }); updateScopeSummary();
      } }); all.indeterminate = selectedSourceIds().length > 0 && selectedSourceIds().length < sources().length;
      sourceCard.append(el('div', { class: 'file-toolbar' }, el('label', { class: 'inline-row' }, all, '本轮全选'), el('span', { id: 'archive-selection-count', text: `本轮已选 ${selectedSourceIds().length} / ${sources().length} 份` })),
        el('div', { class: 'table-scroll' }, el('table', { class: 'source-table' }, el('thead', {}, el('tr', {}, el('th', { text: '本轮' }), el('th', { text: '资料名称' }), el('th', { text: '类型' }), el('th', { text: '状态' }))), el('tbody', {}, ...sources().map(source => el('tr', {}, el('td', {}, el('input', { type: 'checkbox', 'data-source-id': source.id, 'aria-label': `本轮整理 ${source.filename || source.name || source.id}`, checked: state.sourceSelection.has(source.id), onchange: e => {
          e.target.checked ? state.sourceSelection.add(source.id) : state.sourceSelection.delete(source.id); saveSourceSelection(); updateScopeSummary();
        } })), el('td', { text: source.filename || source.name || source.id }), el('td', { text: kindNames[source.kind] || source.kind || '课程资料' }), el('td', { class: `source-status-${source.status}`, text: ({ complete: '已导入', partial: '部分已导入', pending: '待处理', failed: '未完成', error: '需要检查' }[source.status] || source.status || '已留存') })))))));
    }
    else sourceCard.append(el('p', { class: 'small-text muted', text: '还没有导入资料。添加文件并提交任务后，资料会归入这门课程。' }));
    const jobCard = el('section', { class: 'card' }, section('整理任务', '已完成内容会持续保留，可随时继续')); const allJobs = jobs();
    if (allJobs.length) jobCard.append(el('div', { class: 'job-list' }, ...allJobs.slice(0, 8).map(job => {
      const status = jobStatus(job);
      return el('div', { class: 'job-item' }, el('div', { class: 'inline-row' }, el('h4', { text: job.kind === 'question' ? '知识答疑' : job.kind === 'review' ? '作答评阅' : job.kind === 'challenge' ? '答案重审' : '课程资料整理' }), badge(jobLabels[status] || status, status === 'completed' || status === 'complete' ? 'teal' : ['interrupted', 'failed', 'error'].includes(status) ? 'orange' : 'blue')),
        el('p', { text: job.authorization_required ? '等待你允许 Codex（OpenAI）处理这些资料，确认前不会开始。' : job.message || job.progress_message || (['waiting_agent', 'queued'].includes(status) ? runner.available ? '任务已进入队列，自动 Agent 将接单；当前阶段完成时间在接单后估算。' : '任务已保存，等待当前对话 Agent 接手。' : job.request || '') }), jobProgress(job), jobErrors(job), el('div', { class: 'inline-row section-spacer' }, el('span', { class: 'subtle', text: dateLabel(job.updated_at || job.created_at || job.at) }), job.authorization_required && button('允许 Codex 处理', () => openConsentDialog(job), 'primary small'), job.kind === 'build' && ['interrupted', 'partial', 'error', 'failed'].includes(status) && button('继续整理', e => busy(e.currentTarget, () => submitBuild(job)), 'secondary small'), state.detail.preview && button('预览已完成章节', () => { state.preview = true; navigate('chapters'); }, 'text')));
    })));
    else jobCard.append(el('p', { class: 'small-text muted', text: '提交的整理任务和答疑任务会显示在这里。' }));
    return el('div', {}, heading('资料与设置', '先选好目标，再把资料交给你的复习助手。', [badge(modeText(course.mode), course.mode)]), activeJobs()[0] && jobBanner(activeJobs()[0]), el('div', { class: 'grid-two' }, el('div', { class: 'stack' }, fileContainer, sourceCard), el('div', { class: 'stack' }, settings, runnerCard, jobCard)));
  }
  function settingsCard(course) {
    const form = el('form'); let mode = course.mode || 'high';
    const radioOptions = el('div', { class: 'mode-grid' });
    for (const [value, title, subtitle] of [['high', '高分模式', '完整脉络 · 理解与应用'], ['pass', '速通模式', '先学重点 · 高效过关']]) {
      const label = el('label', { class: `mode-option${mode === value ? ' selected' : ''}` });
      label.append(el('input', { type: 'radio', name: 'mode', value, checked: mode === value, onchange: () => { mode = value; radioOptions.querySelectorAll('.mode-option').forEach(x => x.classList.remove('selected')); label.classList.add('selected'); } }), el('span', {}, el('strong', { text: title }), el('small', { text: subtitle }))); radioOptions.append(label);
    }
    const max = el('input', { type: 'number', min: 1, step: 'any', value: course.score_max || 100, required: true }); const pass = el('input', { type: 'number', min: 1, step: 'any', value: course.passing_score || 60, required: true });
    const save = el('button', { type: 'submit', class: 'button secondary small', text: '保存复习目标' });
    form.append(radioOptions, el('div', { class: 'form-grid' }, el('label', {}, '卷面满分', max), el('label', {}, '卷面及格线', pass)), save, el('p', { class: 'settings-description', text: '速通模式优先整理教师重点和基础得分知识。练习表现与自评记录会单独保留，不能替代真实考试成绩。' }));
    form.addEventListener('submit', e => { e.preventDefault(); busy(save, async () => {
      if (!(+pass.value > 0 && +max.value >= +pass.value)) throw new Error('及格线应大于零且不超过卷面满分。');
      await api('/api/mode', { course_id: state.courseId, mode, score_max: +max.value, passing_score: +pass.value }); toast('复习目标已保存，后续整理将使用新目标。'); await refreshCourse();
    }); });
    return el('section', { class: 'card' }, section('本轮复习目标', '目标可以调整，学习记录会继续保留'), form);
  }
  function mergeFiles(files) {
    for (const value of list(files)) { const file = typeof value === 'string' ? { path: value, name: value.split(/[\\/]/).pop() } : value; if (!file.path) continue; if (!state.files.some(f => f.path === file.path)) state.files.push(file); state.selectedFiles.add(file.path); }
  }
  async function pickFiles(kind) {
    const input = el('input', { type: 'file', multiple: true, hidden: true });
    if (kind === 'directory') { input.setAttribute('webkitdirectory', ''); input.setAttribute('directory', ''); }
    else input.accept = kind === 'audio' ? '.mp3,.m4a,.wav,.aac,.flac,.ogg,.mp4' : '.ppt,.pptx,.pdf,.docx,.txt,.md,.markdown,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff';
    const selected = await new Promise(resolve => {
      input.addEventListener('change', () => { const files = [...input.files]; input.remove(); resolve(files); }, { once: true });
      input.addEventListener('cancel', () => { input.remove(); resolve([]); }, { once: true });
      document.body.append(input); input.click();
    });
    const files = selected.filter(file => /\.(pptx?|pdf|docx|txt|md|markdown|png|jpe?g|webp|bmp|tiff?|mp3|m4a|wav|aac|flac|ogg|mp4)$/i.test(file.name));
    for (let n = 0; n < files.length; n++) {
      const file = files[n]; toast(`正在添加资料 ${n + 1} / ${files.length}…`);
      const address = `/api/upload?${new URLSearchParams({ course_id: state.courseId, filename: file.name })}`;
      const response = await fetch(address, { method: 'POST', headers: { 'X-Session-Token': state.token, 'Content-Type': 'application/octet-stream' }, body: file });
      const result = await response.json(); if (!response.ok || !result.ok) throw new Error(result.error || '文件添加失败');
      const saved = result.data; if (kind === 'audio') saved.kind = 'audio'; mergeFiles([saved]);
    }
    if (selected.length) { toast(`已在本地添加 ${files.length} 份资料${kind === 'directory' ? '，无需再次扫描' : ''}。`); render(); }
  }
  async function scanDirectory(path) {
    if (!path.trim()) throw new Error('请先选择资料文件夹。');
    state.materialPath = path.trim(); const response = await api('/api/scan', { path: state.materialPath }); mergeFiles(response.files || response); toast(`找到 ${list(response.files || response).length} 份文件，请勾选本次要整理的资料。`); render();
  }
  async function submitBuild(resumeJob = null) {
    if (resumeJob && typeof resumeJob === 'object') {
      const job = await api('/api/jobs', { course_id: state.courseId, kind: 'build', settings: { resume_job_id: resumeJob.id } });
      toast(job.authorization_required ? '续做任务已保留，等待资料处理授权。' : '已提交续做，已完成的章节会继续保留。'); await refreshCourse(); maybeOpenConsent(job); return;
    }
    const files = Array.from(state.selectedFiles); const sourceIds = selectedSourceIds();
    if (!files.length && !sourceIds.length) throw new Error('本轮尚未选择资料。请勾选已归档资料，或扫描并选择待导入文件。');
    const job = await api('/api/jobs', { course_id: state.courseId, files, source_ids: sourceIds, request: state.request.trim(), kind: 'build', settings: { file_kinds: Object.fromEntries(state.files.filter(f => state.selectedFiles.has(f.path)).map(f => [f.path, kindFor(f)])) } });
    toast(job.authorization_required ? '任务已保存，等待你授权 Codex 处理选中资料。' : job?.reused ? '已有整理任务正在等待或处理中，继续沿用该任务原有范围；本轮选择已保留。' : `任务已保存：已归档 ${sourceIds.length} 份 + 待导入 ${files.length} 份，${state.detail?.runner?.available ? '自动 Agent 正在接单' : '等待当前对话 Agent 接手'}。`); await refreshCourse(); maybeOpenConsent(job);
  }

  const focusTypes = {
    exam: { title: '教师明确会考', short: '明确会考', description: '保留教师的明确表述和原始来源' },
    priority: { title: '教师强调重点', short: '教师重点', description: '教师要求重点掌握，不等同于保证会考' },
    user: { title: '你指定的重点', short: '用户指定', description: '按你的复习目标主动关注' },
    suggested: { title: '建议优先学习', short: '建议先学', description: '依据资料与知识依赖给出的复习建议' },
  };
  function focusType(item) {
    const kind = item.category || item.type || item.source_type || item.level || item.label || '';
    if (['user', 'user_priority', 'user_designated', '用户指定'].includes(kind) || item.claim_type === 'user_priority') return 'user';
    if (['teacher_exam', 'exam', 'teacher_explicit_exam', '教师明确会考', '必考'].includes(kind) || kind === 'teacher_explicit' && ['exam', 'exam_assured', 'exam_claim', 'will_exam', 'guaranteed'].includes(item.claim_type)) return 'exam';
    if (['teacher_priority', 'priority', 'teacher_explicit', 'teacher_focus', '教师重点', '教师强调重点'].includes(kind)) return 'priority';
    return 'suggested';
  }
  const focusTitle = item => item.topic_title || item.title || item.topic || item.name || list(state.detail?.topics).find(t => t.id === item.topic_id)?.title || '待确认知识点';
  const focusNote = item => item.reason || item.note || item.description || item.rationale || '';
  function allFocus() {
    const authoritative = list(state.detail?.focus); const byId = new Map(authoritative.map(item => [item.id, item]));
    const items = [...authoritative, ...list(currentBundle()?.focus)]; const seen = new Set();
    return items.filter(item => {
      const status = byId.get(item.id)?.status || item.status;
      if (['cancelled', 'revoked', 'inactive', 'superseded', 'withdrawn'].includes(status)) return false;
      const key = `${focusType(item)}:${focusTitle(item)}`; if (seen.has(key)) return false; seen.add(key); return true;
    }).sort((a, b) => Object.keys(focusTypes).indexOf(focusType(a)) - Object.keys(focusTypes).indexOf(focusType(b)));
  }
  function focusPage() {
    const all = allFocus(); const filtered = state.focusFilter === 'all' ? all : all.filter(f => focusType(f) === state.focusFilter);
    const filters = el('div', { class: 'chip-row' }, ...[['all', '全部重点'], ...Object.entries(focusTypes).map(([key, v]) => [key, v.title])].map(([key, text]) => el('button', { type: 'button', class: `filter-chip${state.focusFilter === key ? ' active' : ''}`, onclick: () => { state.focusFilter = key; render(); }, text: `${text} ${key === 'all' ? all.length : all.filter(f => focusType(f) === key).length}` })));
    const cards = el('div', {}, ...filtered.map(item => {
      const type = focusType(item); const evidence = list(item.evidence); const rawRefs = item.source_refs || item.references || evidence;
      return el('article', { class: `focus-card ${type}` }, el('div', { class: 'section-heading' }, el('h3', { text: focusTitle(item) }), badge(focusTypes[type].title, type)), focusNote(item) && markdown(focusNote(item)),
        ...evidence.filter(e => e.quote).slice(0, 4).map(e => el('div', {}, el('blockquote', { text: e.quote }), refs([e]))), !evidence.length && refs(rawRefs), el('div', { class: 'focus-source', text: focusTypes[type].description }));
    }));
    if (!filtered.length) cards.append(el('div', { class: 'card' }, empty('这里还没有这类重点', '教师明确会考、教师重点、你的指定和学习建议会分别保留，避免混淆。', null, 'focus')));
    const addForm = el('form'); const title = el('input', { placeholder: '例如：二叉树的遍历', required: true, maxlength: 200, style: 'width:100%', 'aria-label': '指定重点的标题' }); const note = el('textarea', { placeholder: '写下为什么想优先复习，例如：课堂上没听懂，练习中容易混淆。', required: true, 'aria-label': '指定重点的实际说明' }); const save = el('button', { type: 'submit', class: 'button primary small', text: '保存为我的重点' });
    addForm.append(field('知识点', title), field('补充说明', note), el('div', { class: 'section-spacer' }, save)); addForm.addEventListener('submit', e => { e.preventDefault(); busy(save, async () => { await api('/api/focus', { course_id: state.courseId, title: title.value.trim(), note: note.value.trim() }); toast('已保存为你指定的重点。'); await refreshCourse(); }); });
    return el('div', {}, heading('把注意力，放在有依据的重点上', '同样是“重要”，来源不同，复习时的判断也不同。'), el('div', { class: 'focus-legend' }, ...Object.entries(focusTypes).map(([type, item]) => badge(item.title, type))), el('div', { class: 'grid-two' }, el('div', { class: 'stack' }, filters, cards, focusMethodsCard(all)), el('section', { class: 'card' }, section('补充一个你的重点', '主动指定的内容会清楚标记，不会当作教师原话'), addForm)));
  }
  function focusMethodsCard(focus) {
    let methods = list(currentBundle()?.focus_methods).map(method => ({ ...method, suggested: false }));
    if (!methods.length) {
      const seen = new Set();
      for (const item of focus) {
        const title = focusTitle(item); const normalized = plain(title).replace(/\s/g, '');
        const focusSources = new Set([...list(item.evidence).map(ref => ref.source_id), ...(Array.isArray(item.source_ids) ? item.source_ids : [])].filter(Boolean));
        const candidates = chapters().map(chapter => {
          const text = plain([chapter.title, chapter.summary, ...list(chapter.examples).map(example => example.question || example.title)].join(' ')).replace(/\s/g, '');
          return { chapter, score: normalized && text.includes(normalized) ? 2 : list(chapter.source_refs).some(ref => focusSources.has(ref.source_id)) || (chapter.source_ids || []).some(id => focusSources.has(id)) ? 1 : 0 };
        }).filter(candidate => candidate.score > 0 && list(candidate.chapter.examples).length).sort((a, b) => b.score - a.score);
        const chapter = candidates[0]?.chapter;
        if (!chapter) continue;
        const example = list(chapter.examples)[0]; const key = `${chapter.id}:${example.title || example.question}`;
        if (seen.has(key)) continue; seen.add(key);
        methods.push({ id: `suggested_${chapter.id}_${methods.length}`, topic_title: title, focus_ids: item.id ? [item.id] : [], method_steps: [], example, source_refs: chapter.source_refs, suggested: true, chapter_title: chapter.title });
      }
    }
    const card = el('section', { class: 'card focus-methods' }, section('经典例题与解题方法', '把重点落实到解题步骤，而不仅是记住知识点名称'));
    if (!methods.length) { card.append(el('p', { class: 'small-text muted', text: '目前还没有能与这些重点可靠关联的例题。整理出对应章节例题后，这里会显示解题方法和来源。' })); return card; }
    card.append(el('p', { class: 'action-note', text: '例题用于理解相关重点。关联教师重点不表示这道例题或原题一定会考。' }), ...methods.map(method => {
      const linked = focus.filter(item => (method.focus_ids || []).includes(item.id));
      const highest = linked.sort((a, b) => Object.keys(focusTypes).indexOf(focusType(a)) - Object.keys(focusTypes).indexOf(focusType(b)))[0];
      const type = highest ? focusType(highest) : 'suggested'; const example = method.example || {};
      const details = el('details', { class: 'focus-method' }, el('summary', {}, el('span', { text: method.topic_title || method.title || '重点解题方法' }), badge(method.suggested ? '建议例题' : highest ? `关联${focusTypes[type].short}` : '解题方法', method.suggested ? 'suggested' : type)));
      const body = el('div', { class: 'solution' });
      if (method.suggested) body.append(el('p', { class: 'action-note', text: `从「${method.chapter_title}」的相关例题映射，作为复习建议；未把它标成教师指定考题。` }));
      if (list(method.method_steps).length) body.append(el('h4', { text: '解题方法' }), el('ol', { class: 'method-steps' }, ...list(method.method_steps).map(step => el('li', {}, markdown(typeof step === 'string' ? step : step.text || step.description || plain(step))))));
      if (typeof example === 'string') body.append(el('h4', { class: 'section-spacer', text: '关联例题与过程' }), markdown(example));
      else {
        if (example.question || example.problem) body.append(el('h4', { class: 'section-spacer', text: example.title || '关联例题' }), markdown(example.question || example.problem));
        if (example.solution || example.answer || example.explanation) body.append(el('h4', { class: 'section-spacer', text: '解题过程' }), markdown(example.solution || example.explanation || example.answer));
        else body.append(el('p', { class: 'small-text muted', text: '对应解题过程尚待整理，当前不补造答案。' }));
      }
      body.append(refs(method.source_refs || example.source_refs || [])); details.append(body); return details;
    })); return card;
  }

  function cleanNodeLabel(text) { return plain(text).trim().replace(/^[-*#]+\s*/, '').replace(/^([\w\u4e00-\u9fff-]+)?\(\((.+)\)\)$/, '$2').replace(/^[\w-]+\["?(.*?)"?\]$/, '$1').replace(/^"|"$/g, '').replace(/<[^>]*>/g, '').trim(); }
  function mapTree(source, fallbackTitle) {
    if (source && typeof source === 'object') {
      const convert = item => ({ label: item.label || item.title || item.text || item.name || '知识点', chapter_id: item.chapter_id, children: list(item.children).map(convert) });
      if (Array.isArray(source)) return { label: fallbackTitle || currentCourse().name, children: source.map(convert) };
      return convert(source);
    }
    let text = plain(source).replace(/^\s*```(?:mermaid)?\s*\n?/i, '').replace(/\n?\s*```\s*$/, '').replace(/\r\n?/g, '\n');
    if (/^\s*(?:flowchart|graph)\b/m.test(text)) {
      const nodes = new Map(); const edges = []; const children = new Set();
      const pattern = /([\w\u4e00-\u9fff-]+)\s*(?:\[([^\]]+)\]|\(\(([^)]+)\)\)|\(([^)]+)\)|\{([^}]+)\})/g; let match;
      while ((match = pattern.exec(text))) nodes.set(match[1], cleanNodeLabel(match[2] || match[3] || match[4] || match[5]));
      for (const row of text.split(/[\n;]/)) {
        if (!/(?:-->|---|-.->|==>)/.test(row)) continue;
        const ids = row.split(/-->|---|-.->|==>/).map(part => part.trim().replace(/^\|[^|]*\|\s*/, '').match(/^([\w\u4e00-\u9fff-]+)/)?.[1]).filter(Boolean);
        for (let n = 0; n < ids.length - 1; n++) {
          const [from, to] = [ids[n], ids[n + 1]];
          if (!edges.some(edge => edge[0] === from && edge[1] === to)) edges.push([from, to]); children.add(to);
          if (!nodes.has(from)) nodes.set(from, from); if (!nodes.has(to)) nodes.set(to, to);
        }
      }
      if (nodes.size) {
        const roots = [...nodes.keys()].filter(id => !children.has(id)); if (!roots.length) roots.push(nodes.keys().next().value);
        // A dependency graph may give one node several parents. Draw each node once
        // in this tree and preserve the extra edges as explicit related-node labels.
        const allocated = new Map(); const queue = []; const forest = [];
        function allocate(id) { const node = { label: nodes.get(id) || id, children: [], related: [] }; allocated.set(id, node); queue.push(id); return node; }
        roots.forEach(id => { if (!allocated.has(id)) forest.push(allocate(id)); });
        function drain() {
          while (queue.length) {
            const id = queue.shift(); const node = allocated.get(id);
            for (const [, to] of edges.filter(edge => edge[0] === id)) {
              if (!allocated.has(to)) node.children.push(allocate(to));
              else if (to !== id) node.related.push(nodes.get(to) || to);
            }
          }
        }
        drain();
        for (const id of nodes.keys()) if (!allocated.has(id)) { forest.push(allocate(id)); drain(); }
        return forest.length === 1 ? forest[0] : { label: fallbackTitle || currentCourse().name || '课程知识脉络', children: forest };
      }
    }
    const rows = text.split('\n').filter(line => line.trim() && !/^\s*(?:mindmap|%%|flowchart|graph|style|classDef|class |linkStyle|direction )\b/.test(line));
    const root = { label: fallbackTitle || currentCourse().name || '知识脉络', children: [] }; const stack = [{ indent: -1, node: root }];
    rows.forEach(line => {
      const indent = (line.match(/^\s*/)[0].replace(/\t/g, '    ').length) + (line.match(/^\s*(#+)/)?.[1].length || 0);
      const label = cleanNodeLabel(line); if (!label || label.startsWith('%%') || label.startsWith('}%%')) return; const node = { label, children: [] };
      while (stack.length > 1 && indent <= stack[stack.length - 1].indent) stack.pop(); stack[stack.length - 1].node.children.push(node); stack.push({ indent, node });
    });
    if (root.children.length === 1) return root.children[0];
    if (root.children.length) return root;
    return { label: fallbackTitle || currentCourse().name || '课程知识脉络', children: chapters().map(c => ({ label: c.title, chapter_id: c.id, children: list(c.topics || c.knowledge_points).map(topic => ({ label: topic.title || topic.name || plain(topic), children: [] })) })) };
  }
  let diagramCounter = 0; let mermaidInitialized = false;
  function mindmap(source, title = '') {
    const root = mapTree(source, title); const container = el('div'); const canvas = el('div', { class: 'mindmap' }); let graphRendered = false;
    function buildNode(node, depth) {
      const children = list(node.children); const ul = children.length ? el('ul', {}, ...children.map(child => buildNode(child, depth + 1))) : null;
      const nodeButton = el('button', { class: 'map-node', type: 'button', title: list(node.related).length ? '还关联：' + node.related.join('、') : null, ...(children.length ? { 'aria-expanded': 'true' } : {}), onclick: () => {
        if (ul) { ul.hidden = !ul.hidden; nodeButton.setAttribute('aria-expanded', String(!ul.hidden)); nodeButton.lastChild.textContent = ul.hidden ? `+${children.length}` : '−'; }
        else if (node.chapter_id) navigate('chapters', node.chapter_id);
      } }, node.label || '知识点', children.length > 0 ? el('span', { class: 'fold-indicator', text: '−' }) : null);
      return el('li', {}, nodeButton, Array.isArray(node.related) && node.related.length > 0 ? el('span', { class: 'map-related', text: '关联：' + node.related.join('、') }) : null, ul);
    }
    const tree = el('ul', { class: 'map-tree' }, buildNode(root, 0)); canvas.append(tree);
    const controls = el('div', { class: 'map-toolbar' }, el('span', { class: 'map-tree-summary', text: '点击分支可折叠，先看主线，再展开细节。' }), el('div', { class: 'inline-row' },
      button('展开全部', () => { tree.querySelectorAll('ul').forEach(ul => { ul.hidden = false; }); tree.querySelectorAll('[aria-expanded]').forEach(b => { b.setAttribute('aria-expanded', 'true'); b.lastChild.textContent = '−'; }); }, 'secondary small'),
      button('收起分支', () => { tree.querySelectorAll('ul ul').forEach(ul => { ul.hidden = true; }); tree.querySelectorAll('li li>.map-node[aria-expanded]').forEach(b => { b.setAttribute('aria-expanded', 'false'); b.lastChild.textContent = `+${b.nextElementSibling?.children.length || ''}`; }); }, 'secondary small')));
    if (window.mermaid && typeof source === 'string' && /(?:mindmap|flowchart|graph)\b/.test(source)) controls.lastChild.append(button('图形 / 层级视图', e => busy(e.currentTarget, async () => {
      if (graphRendered) { canvas.querySelector('img')?.remove(); tree.hidden = false; graphRendered = false; return; }
      try {
        if (!mermaidInitialized) { window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', maxTextSize: 50000, suppressErrorRendering: true, theme: 'base', fontFamily: 'Microsoft YaHei, Segoe UI, sans-serif', themeVariables: { primaryColor: '#e6f4ef', primaryTextColor: '#386b62', primaryBorderColor: '#a0c7bc', lineColor: '#9bbcb4', secondaryColor: '#eaf2fb', tertiaryColor: '#f7fafb' }, flowchart: { htmlLabels: false }, mindmap: { useMaxWidth: true } }); mermaidInitialized = true; }
        const clean = source.replace(/%%\{[\s\S]*?\}%%/g, '').replace(/^\s*---[\s\S]*?\n---\s*\n/, '').replace(/^\s*```(?:mermaid)?\s*\n?/i, '').replace(/\n?\s*```\s*$/, '');
        if (clean.length > 50000) throw new Error('导图过长');
        const result = await window.mermaid.render(`study_diagram_${++diagramCounter}`, clean);
        if (!canvas.isConnected) return;
        const svgDoc = new DOMParser().parseFromString(result.svg, 'image/svg+xml');
        svgDoc.querySelectorAll('script, foreignObject, iframe, object, embed').forEach(n => n.remove());
        for (const n of svgDoc.querySelectorAll('*')) for (const attr of [...n.attributes]) if (/^on/i.test(attr.name) || /^(?:href|xlink:href)$/i.test(attr.name) && !attr.value.startsWith('#')) n.removeAttribute(attr.name);
        const blobUrl = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svgDoc)], { type: 'image/svg+xml' }));
        const image = el('img', { src: blobUrl, alt: `${title || currentCourse().name}知识脉络导图`, 'data-blob-url': blobUrl }); canvas.append(image); tree.hidden = true; graphRendered = true;
      } catch { toast('图形暂时无法绘制，已保留完整的层级脉络。'); }
    }), 'secondary small'));
    container.append(controls, canvas, el('p', { class: 'map-caption', text: '导图帮助串起知识，重点与出处可在对应章节中查看。' })); return container;
  }
  function mapPage() {
    const bundle = currentBundle(); const all = chapters(); const selected = all.find(c => c.id === state.mapChapter);
    const select = el('select', { class: 'scope-select', 'aria-label': '选择导图范围', onchange: e => { state.mapChapter = e.target.value; render(); } }, el('option', { value: '', text: '全课程 · 知识脉络' }), ...all.map(c => el('option', { value: c.id, text: c.title }))); select.value = state.mapChapter;
    const page = el('div', {}, heading('先有全貌，再看细节', '用知识之间的联系，代替零散地记住每一页。'), previewRibbon());
    if (!bundle) page.append(el('div', { class: 'card' }, empty('知识脉络还在等你的资料', '导入资料并完成整理后，可以查看课程总图和每一章的知识关系。', button('导入资料', () => navigate('materials'), 'primary'), 'map')));
    else {
      page.append(el('div', { class: 'card' }, section(selected?.title || currentCourse().name || '课程知识脉络', '主线 → 概念 → 应用，逐层展开', select), mindmap(selected?.mindmap || bundle.mindmap || '', selected?.title || currentCourse().name)));
      if (selected) page.append(button('去学习这一章', () => navigate('chapters', selected.id), 'primary spacious', 'arrow'));
    }
    return page;
  }

  function chapterMenu() {
    const active = activeChapter();
    return el('aside', { class: 'card chapter-menu', 'aria-label': '章节列表' }, ...chapters().map((chapter, n) => el('button', { class: `chapter-tab${active?.id === chapter.id ? ' active' : ''}`, type: 'button', onclick: () => { state.chapterId = chapter.id; remember(`chapter_${state.courseId}`, chapter.id); render(); window.scrollTo({ top: 0, behavior: 'instant' }); } }, el('span', { text: `${String(n + 1).padStart(2, '0')}  ${chapter.title}` }), el('br'), badge(progressLabels[progressFor(chapter.id).status] || (chapter.status === 'pending' ? '正在整理' : '未开始'), hasRead(chapter.id) ? 'teal' : ''))));
  }
  function chaptersPage() {
    const chapter = activeChapter(); const page = el('div', {}, heading('把概念，学到能讲清楚', '按章节理解主线，遇到问题再看例题和解析。', [badge(`复习目标 · ${modeText(currentCourse().mode)}`, currentCourse().mode)]), previewRibbon());
    if (!chapter) { const hasSources = sources().length > 0; page.append(el('div', { class: 'card' }, empty(hasSources ? '资料已就绪，等待生成复习章节' : '还没有可阅读的章节', hasSources ? '前往资料与设置选择本轮整理范围，再生成复习资料。已提交的任务状态也可在那里查看。' : '先导入课程资料，整理完成的章节会逐步出现在这里。', button(hasSources ? '生成复习资料' : '导入课程资料', () => navigate('materials'), 'primary'), 'chapters'))); return page; }
    const content = el('div', { class: 'chapter-content' }); const chapterNumber = Math.max(1, chapters().findIndex(item => item.id === chapter.id) + 1);
    const card = el('article', { class: 'card' }, el('div', { class: 'inline-row space-between' }, el('p', { class: 'eyebrow', text: `CHAPTER ${String(chapterNumber).padStart(2, '0')}` }), badge(progressLabels[progressFor(chapter.id).status] || '尚未记录阅读', hasRead(chapter.id) ? 'teal' : '')), el('h2', { text: chapter.title }), chapter.summary && el('div', { class: 'chapter-summary' }, markdown(chapter.summary)));
    if (list(chapter.warnings).length) card.append(notice(list(chapter.warnings).map(plain).join('；'), 'warning'));
    if (chapter.explanation || chapter.content) card.append(markdown(chapter.explanation || chapter.content));
    else card.append(notice('这一章的详细讲解尚未整理完成。已完成的摘要可以先预览，后续内容会保留在同一章节中。', 'warning'));
    if (chapter.source_refs) card.append(refs(chapter.source_refs));
    card.append(el('div', { class: 'reading-toolbar' }, button('标记已读', e => busy(e.currentTarget, () => saveProgress(chapter.id, 'read')), 'secondary small', 'check'), button('我已理解', e => busy(e.currentTarget, () => saveProgress(chapter.id, 'self_assessed')), 'ghost small'), button('仍需复习', e => busy(e.currentTarget, () => saveProgress(chapter.id, 'needs_review')), 'secondary small'), button('练习这一章 →', () => { Object.values(state.practiceScopes).forEach(scope => { scope.chapter = chapter.id; scope.mistakesOnly = false; scope.index = 0; }); state.practiceOrigin = null; state.questionIndex = 0; navigate('practice'); }, 'text')),
      el('p', { class: 'action-note', text: '“我已理解”保存为自评；独立练习是否通过会另外记录。' })); content.append(card);
    if (!chapter.explanation && !chapter.content) card.querySelectorAll('.reading-toolbar button:not(.text)').forEach(control => { control.disabled = true; });
    if (list(chapter.examples).length) content.append(el('section', { class: 'card' }, section('沿着例题，走完一次思路'), ...list(chapter.examples).map((example, n) => el('div', { class: 'example-card' }, el('h4', { text: example.title || `例题 ${n + 1}` }), markdown(example.question || example.description || example.problem), el('details', { class: 'section-spacer' }, el('summary', { text: '展开解题过程' }), el('div', { class: 'solution' }, markdown(example.solution || example.explanation || example.answer || '这道例题的解题过程尚未整理。')))))));
    if (list(chapter.pitfalls).length) content.append(el('section', { class: 'card' }, section('这些地方，值得多留意一下'), el('ul', { class: 'pitfall-list' }, ...list(chapter.pitfalls).map(p => el('li', { text: typeof p === 'string' ? p : p.warning || p.description || p.title || plain(p) })))));
    if (chapter.mindmap) content.append(el('section', { class: 'card' }, section('这一章的知识关系'), mindmap(chapter.mindmap, chapter.title)));
    content.append(questionRequestCard(chapter)); page.append(el('div', { class: 'chapter-layout' }, chapterMenu(), content)); return page;
  }
  async function saveProgress(chapterId, status) {
    await api('/api/progress', { course_id: state.courseId, package_id: currentBundle()?.manifest?.id, chapter_id: chapterId, status }); toast(status === 'self_assessed' ? '已保存你的理解自评。' : status === 'needs_review' ? '已加入待复习记录。' : '本章已标记为读过。'); await refreshCourse();
  }
  function questionRequestCard(chapter) {
    const area = el('textarea', { placeholder: `关于${chapter ? '「' + chapter.title + '」' : '这门课'}，你还想弄清楚什么？`, value: state.customQuestion, 'aria-label': '向 Agent 提交问题', oninput: e => { state.customQuestion = e.target.value; } });
    const send = button('提交答疑问题', e => busy(e.currentTarget, async () => {
      if (!area.value.trim()) throw new Error('先写下你想弄清楚的问题。');
      const job = await api('/api/jobs', { course_id: state.courseId, kind: 'question', request: `${chapter ? '当前章节：' + chapter.title + '\n' : ''}${area.value.trim()}`, settings: { chapter_id: chapter?.id, package_id: currentBundle()?.manifest?.id } }); state.customQuestion = ''; toast(job.authorization_required ? '问题已保存，等待资料处理授权。' : '问题已保存，等待 Agent 回答。'); await refreshCourse(); maybeOpenConsent(job);
    }), 'secondary small');
    const responses = list(state.detail.responses).filter(r => !chapter || !r.chapter_id || r.chapter_id === chapter.id);
    return el('section', { class: 'card' }, section('还有一个地方没想通？', '先留下问题，Agent 的回答会保存在这门课里'), area, el('div', { class: 'section-spacer' }, send), el('p', { class: 'action-note', text: '提交问题会创建答疑任务；阅读已有内容和展开解析不创建新任务。' }),
      ...responses.slice(-4).reverse().map(response => el('details', { class: 'section-spacer' }, el('summary', { text: response.request || '已完成的答疑' }), el('div', { class: 'solution' }, markdown(response.text || response.answer || response.response), el('span', { class: 'subtle', text: dateLabel(response.at) })))));
  }

  function questionKey(question) { return `${question.id}:${question.reference_version || ''}:${currentBundle()?.manifest?.id || ''}`; }
  function allQuestions() {
    const bundle = currentBundle(); const seen = new Set(); const values = [...list(bundle?.practice), ...chapters().flatMap(c => list(c.questions).map(q => ({ chapter_id: c.id, ...q }))), ...list(state.detail?.original_questions)];
    return values.filter(q => { if (!q.id || seen.has(q.id)) return false; seen.add(q.id); return true; });
  }
  const questionOriginKey = question => question.origin === 'ai_generated' ? 'agent' : 'provided';
  const questionOrigin = question => questionOriginKey(question) === 'agent' ? 'Agent 模拟题' : '用户提供的题目';
  function questionAttempts(question) {
    return list(state.detail?.attempts).filter(attempt => attempt.question_id === question.id && (!question.reference_version || attempt.reference_version === question.reference_version));
  }
  function alreadyViewed(question) {
    const view = state.detail?.question_views?.[question.id];
    if (question.reference_version && view?.versions) return !!view.versions[question.reference_version]?.answer_viewed || state.revealed.has(questionKey(question)) || questionAttempts(question).length > 0;
    if (view && question.reference_version && view.reference_version && view.reference_version !== question.reference_version) return false;
    return !!(view?.answer_viewed || state.revealed.has(questionKey(question)) || questionAttempts(question).length);
  }
  function safeQuestionText(question) {
    // Keep the original wording. If the stem contains an answer, mark this as a walkthrough.
    return question.question || question.prompt || question.stem || '题面尚未整理';
  }
  function challengeJobs(question) {
    return jobs().filter(job => {
      const settings = job.settings || {};
      return (job.kind === 'challenge' || settings.action === 'challenge' || settings.review_type === 'answer_challenge' || settings.challenge === true)
        && (settings.question_id === question.id || job.question_id === question.id)
        && (!settings.package_id || settings.package_id === currentBundle()?.manifest?.id);
    });
  }
  function pendingChallenge(question) {
    const live = challengeJobs(question).find(job => ['awaiting_authorization', 'waiting_agent', 'queued', 'pending', 'running', 'processing'].includes(jobStatus(job)));
    if (live) return live;
    const local = state.challenges.get(question.id);
    if (!local) return null;
    const current = jobs().find(job => job.id === local.id);
    if (current && ['complete', 'completed', 'failed', 'error', 'cancelled'].includes(jobStatus(current))) return null;
    return local;
  }
  function answerProvenance(question) {
    const value = question.answer_origin || (typeof question.answer_provenance === 'object' ? question.answer_provenance?.origin || question.answer_provenance?.type || question.answer_provenance?.source_type : question.answer_provenance);
    if (['source', 'material', 'provided', 'user_provided', 'teacher', 'official', '资料提供'].includes(value)) return { label: '资料提供', color: 'teal', source: true };
    if (value === 'agent_rechecked') return { label: 'Agent 重审 · 本次核对结论', color: 'blue', source: false };
    if (['agent_generated', 'ai_generated', 'agent', 'ai'].includes(value)) return { label: 'Agent 生成 · 非资料原答案', color: 'orange', source: false };
    if (question.answer_source || question.answer_source_id) return { label: '资料提供', color: 'teal', source: true };
    if (questionOriginKey(question) === 'agent') return { label: 'Agent 生成 · 模拟题参考答案', color: 'orange', source: false };
    return { label: '答案来源待核对', color: 'outlined', source: false };
  }
  function answerChallengeCard(question) {
    if (!currentBundle()?.manifest?.id) return notice('原题已保留。完整解析生成后，可以在这里质疑并重新核对答案。', 'info');
    const pending = pendingChallenge(question); const reason = el('textarea', { placeholder: '可选：指出哪一步有问题，或给出你认为正确的答案与依据。', 'aria-label': '质疑答案的原因（可选）', value: state.challengeReasons.get(question.id) || '', oninput: event => state.challengeReasons.set(question.id, event.target.value) });
    const body = el('div', { class: 'challenge-form', hidden: true }, el('p', { class: 'small-text muted', text: '重审会核对现有资料与解题过程。原资料答案和旧作答记录保留，不会被覆盖。' }), reason);
    const submit = button('提交答案重审', event => busy(event.currentTarget, async () => {
      const result = await api('/api/challenge', { course_id: state.courseId, package_id: currentBundle()?.manifest?.id, question_id: question.id, reason: reason.value.trim() });
      state.challenges.set(question.id, result.job || result); state.challengeReasons.delete(question.id);
      toast((result.job || result).authorization_required ? '答案质疑已保存，授权后开始核对。' : '答案质疑已提交，等待重审。原资料与历史版本会保留。'); await refreshCourse(); maybeOpenConsent(result.job || result);
    }), 'secondary small'); submit.disabled = !!pending; body.append(submit);
    const toggle = button(pending ? '答案待重审' : '质疑答案', () => { body.hidden = !body.hidden; if (!body.hidden) reason.focus(); }, 'secondary small');
    return el('div', { class: 'answer-challenge' }, el('div', { class: 'inline-row space-between' }, el('span', { class: 'subtle', text: '对参考答案或推导过程有疑问，可以提出核对。' }), toggle), body);
  }
  function answerVersionCard(question) {
    const oldAttempts = list(state.detail?.attempts).filter(attempt => attempt.question_id === question.id && question.reference_version && attempt.reference_version !== question.reference_version);
    const history = list(question.answer_reviews || question.answer_history || question.reference_history || question.revisions);
    const revision = state.answerRevisions.get(question.id); const reviewed = challengeJobs(question).filter(job => ['complete', 'completed'].includes(jobStatus(job)));
    const details = el('details', { class: 'answer-versions' }, el('summary', { text: '本次答案版本与历史说明' }),
      el('p', { class: 'small-text muted', text: question.reference_version ? `当前答案版本：${String(question.reference_version).slice(0, 12)}。判分与已看解析记录按这个版本保存。` : '当前为资料中的参考版本，后续核对将保留原资料与已有作答记录。' }),
      el('p', { class: 'action-note', text: '题目来自用户资料与“答案由谁提供”分别标记。Agent 补充或重审的答案只更新复习包中的核对版本，不覆写原资料。' }));
    if (revision) details.append(el('p', { class: 'small-text muted', text: `本次会话检测到版本更新：${String(revision.previous).slice(0, 8)} → ${String(revision.current).slice(0, 8)}，${dateLabel(revision.at)}。` }));
    if (oldAttempts.length) details.append(el('p', { class: 'small-text muted', text: `保留 ${oldAttempts.length} 次旧版本作答，未直接计作本版本的独立通过。` }));
    if (reviewed.length) details.append(el('p', { class: 'small-text muted', text: `已完成 ${reviewed.length} 次答案重审，最近一次：${dateLabel(reviewed[0].updated_at || reviewed[0].completed_at)}。` }));
    if (question.answer_review) details.append(el('p', { class: 'small-text muted', text: `本次重审：${question.answer_review.decision === 'revise' ? '已修订参考答案与解析' : '已核对并保留参考答案'} · ${dateLabel(question.answer_review.reviewed_at)}` }), markdown(question.answer_review.reason), refs(question.answer_review.source_refs));
    if (history.length) details.append(el('ul', { class: 'version-history' }, ...history.map(item => {
      if (typeof item === 'string') return el('li', { text: item });
      const row = el('li', {}, el('span', { text: [item.title || (item.decision === 'revise' ? '修订参考答案' : item.decision === 'keep' ? '保留答案并记录核对说明' : item.note || '历史核对版本'), item.reference_version && String(item.reference_version).slice(0, 12), (item.reviewed_at || item.at) && dateLabel(item.reviewed_at || item.at)].filter(Boolean).join(' · ') }), item.reason && markdown(item.reason));
      if (item.previous_answer != null || item.answer != null) row.append(el('details', { class: 'section-spacer' }, el('summary', { text: '查看这次重审前后的参考答案' }), el('div', { class: 'solution' }, el('h4', { text: '重审前版本' }), markdown(item.previous_answer ?? '未记录'), el('h4', { class: 'section-spacer', text: '本次核对版本' }), markdown(item.answer ?? '未记录'), refs(item.source_refs))));
      return row;
    })));
    return details;
  }
  function practicePage() {
    const questions = allQuestions(); const courseChapters = chapters();
    if (!['provided', 'agent'].includes(state.practiceOrigin)) {
      const chapter = state.practiceScopes.provided.chapter;
      state.practiceOrigin = questions.some(q => questionOriginKey(q) === 'provided' && (chapter === 'all' || q.chapter_id === chapter || q.chapter === chapter)) ? 'provided' : questions.some(q => questionOriginKey(q) === 'agent') ? 'agent' : 'provided';
    }
    const scope = state.practiceScopes[state.practiceOrigin];
    const originQuestions = questions.filter(q => questionOriginKey(q) === state.practiceOrigin);
    let filtered = scope.chapter === 'all' ? originQuestions : originQuestions.filter(q => q.chapter_id === scope.chapter || q.chapter === scope.chapter);
    if (scope.mistakesOnly) filtered = filtered.filter(q => questionAttempts(q).some(a => ['wrong', 'unsure', 'review_required', 'incorrect'].includes(a.result || a.status)));
    state.questionIndex = Math.max(0, Math.min(scope.index, filtered.length - 1)); scope.index = state.questionIndex; const question = filtered[state.questionIndex];
    const tabs = el('div', { class: 'practice-tabs', role: 'tablist', 'aria-label': '练习题来源' }, ...[['provided', '用户提供的题目'], ['agent', 'Agent 模拟题']].map(([origin, title]) => el('button', { type: 'button', role: 'tab', class: `practice-tab${state.practiceOrigin === origin ? ' active' : ''}`, 'aria-selected': state.practiceOrigin === origin ? 'true' : 'false', 'aria-controls': 'practice-content', onclick: () => { state.practiceOrigin = origin; render(); }, onkeydown: event => { if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); state.practiceOrigin = origin === 'provided' ? 'agent' : 'provided'; render(); $('[role=tab][aria-selected=true]')?.focus(); } } }, title, el('span', { text: questions.filter(q => questionOriginKey(q) === origin).length }))));
    const filter = el('select', { class: 'scope-select', 'aria-label': '按章节筛选当前来源的题目', onchange: e => { scope.chapter = e.target.value; scope.index = 0; render(); } }, el('option', { value: 'all', text: `全部章节（${originQuestions.length} 题）` }), ...courseChapters.map(c => el('option', { value: c.id, text: c.title }))); filter.value = scope.chapter;
    const toolbar = el('div', { class: 'practice-filter-row' }, filter, el('button', { type: 'button', class: `filter-chip${scope.mistakesOnly ? ' active' : ''}`, 'aria-pressed': String(scope.mistakesOnly), onclick: () => { scope.mistakesOnly = !scope.mistakesOnly; scope.index = 0; render(); }, text: '只看错题 / 待评阅' }), el('span', { class: 'subtle', text: `当前范围 ${filtered.length} 题` }));
    const page = el('div', {}, heading('让会看，变成会做', '先尝试作答，再对照解析找出卡住的那一步。'), previewRibbon(), tabs, toolbar);
    if (!question) { page.append(el('div', { id: 'practice-content', class: 'card', role: 'tabpanel' }, empty(scope.mistakesOnly ? '这个范围还没有需要回看的错题' : state.practiceOrigin === 'provided' ? '这个范围还没有用户提供的题目' : '这个范围还没有 Agent 模拟题', state.practiceOrigin === 'provided' ? '往年试卷、自测题和课程作业都可以加入资料。整理后，原题与来源会一起保留。' : '完成资料整理后，Agent 生成的模拟练习会单独留在这个标签中，不会冒充资料原题。', button('查看课程资料', () => navigate('materials'), 'primary'), 'practice'))); return page; }
    const key = questionKey(question); const attempts = questionAttempts(question); const latest = state.answers.get(key) || attempts[attempts.length - 1]; const viewed = alreadyViewed(question);
    const leaked = question.answer_visible_in_stem === true || question.independent_eligible === false || typeof question.answer_visible_in_stem !== 'boolean' && (/(?:\([A-Fa-f]\)|\[[A-Fa-f]\]|（[A-Fa-f]）|【[A-Fa-f]】)/.test(plain(question.question)) || /(?:参考答案|正确答案|答案)\s*[:：]\s*\S/.test(plain(question.question)));
    const top = el('div', { class: 'question-topline' }, el('div', { class: 'inline-row' }, badge(questionOrigin(question), questionOriginKey(question) === 'agent' ? 'orange' : 'blue'), badge(question.type === 'choice' || list(question.options).length ? '选择题' : question.type === 'true_false' ? '判断题' : '开放作答', 'outlined')), el('span', { class: 'question-number', text: `${state.questionIndex + 1} / ${filtered.length} 题` }));
    const pendingReview = pendingChallenge(question);
    const card = el('article', { id: 'practice-content', role: 'tabpanel', class: 'card question-card' }, top, leaked && notice(question.practice_note || '原题题干已含参考答案，按讲解题记录，不作为独立练习通过的依据。', 'warning'), question.answer_status === 'needs_review' && notice(question.answer_note || '资料答案与 Agent 候选答案存在分歧。保留资料答案优先展示，候选内容尚未作为确定答案，需进一步核对。', 'warning'), state.answerRevisions.has(question.id) && notice('这道题已有新的答案核对版本。旧作答仍保留，不直接沿用为新版本的独立练习通过；请重新查看参考解析。', 'warning'), pendingReview && notice(pendingReview.authorization_required ? '答案重审正在等待你的资料处理授权，确认后才会开始核对。' : '已提交答案重审，正在等待核对。当前版本与原资料会保留，重审完成后刷新显示新的结论。', 'warning'), markdown(safeQuestionText(question)));
    const form = el('form'); let getResponse; let firstInput;
    if (list(question.options).length) {
      const opts = list(question.options); const optionBox = el('div', { class: 'question-options' });
      opts.forEach((option, index) => {
        const text = typeof option === 'string' ? option : option.text || option.label || option.value || plain(option); const letter = /^[A-H][.、．:：)）\s]/.test(text) ? text.charAt(0) : String.fromCharCode(65 + index);
        const radio = el('input', { type: 'radio', name: 'response', value: letter, required: true }); if (!firstInput) firstInput = radio;
        optionBox.append(el('label', { class: 'question-option' }, radio, el('span', {}, inline(el('span'), text.startsWith(letter) ? text : `${letter}. ${text}`))));
      }); form.append(optionBox); getResponse = () => form.querySelector('input[name=response]:checked')?.value || '';
    } else if (question.type === 'true_false') {
      const select = el('select', { 'aria-label': '选择判断结果', required: true }, el('option', { value: '', text: '请选择判断结果' }), el('option', { value: '正确', text: '正确' }), el('option', { value: '错误', text: '错误' })); form.append(el('div', { class: 'section-spacer' }, select)); firstInput = select; getResponse = () => select.value;
    } else { const answer = el('textarea', { class: 'answer-area', placeholder: '写下你的答案，或先把解题思路说清楚…', 'aria-label': '我的作答', required: true }); form.append(answer); firstInput = answer; getResponse = () => answer.value.trim(); }
    const hint = el('input', { type: 'checkbox', checked: viewed || leaked });
    const submit = el('button', { type: 'submit', class: 'button primary small', text: leaked ? '保存作答记录' : '提交作答' });
    const reveal = button(viewed ? '再次查看解析' : '查看参考解析', e => busy(e.currentTarget, async () => {
      const result = await api('/api/reveal', { course_id: state.courseId, question_id: question.id, package_id: currentBundle()?.manifest?.id, reference_version: question.reference_version });
      state.revealed.set(key, result); await refreshCourse();
    }), 'secondary small');
    form.append(el('div', { class: 'question-actions' }, el('label', { class: 'hint-check' }, hint, viewed ? '此前已查看过参考解析' : leaked ? '题干已含答案，按讲解题记录' : '这次借助了提示 / 资料'), submit, reveal));
    form.addEventListener('submit', e => { e.preventDefault(); busy(submit, async () => {
      const response = getResponse(); if (!response) throw new Error('请先写下答案或选择一个选项。');
      const result = await api('/api/answer', { course_id: state.courseId, question_id: question.id, package_id: currentBundle()?.manifest?.id, reference_version: question.reference_version, response, used_hint: hint.checked || viewed || leaked });
      state.answers.set(key, result); await refreshCourse(); toast(result.review_job?.authorization_required ? '作答已保存，评阅任务等待资料处理授权。' : ['unsure', 'review_required'].includes(result.result || result.status) ? '作答已保存，等待 Agent 评阅。' : '作答记录已保存。'); if (result.review_job) maybeOpenConsent(result.review_job);
    }); });
    card.append(form, el('div', { class: 'question-source', text: '独立作答、看过解析后的练习和自评会分别记录。' }), refs(question.source_refs || question.reference_ids?.map(id => ({ source_id: id })) || (question.source ? [question.source] : [])));
    if (latest || viewed) {
      const full = { ...question, ...(state.revealed.get(key) || {}) }; const feedback = latest?.feedback || {}; const result = latest?.result || latest?.status;
      const reviewing = ['unsure', 'review_required', 'pending_review'].includes(result); const wrong = ['wrong', 'incorrect'].includes(result);
      const label = !latest ? '参考解析 · 已记录为看过答案' : reviewing ? '需要 Agent 评阅 · 暂不判定掌握' : wrong ? '这一步还需要再练一次' : ['right', 'correct', 'passed'].includes(result) ? latest.independent === false || leaked ? '答案核对通过 · 辅助练习记录' : '本题独立作答通过' : '作答已记录';
      const panel = el('section', { class: `answer-panel${reviewing ? ' review' : wrong ? ' wrong' : ''}` }, el('strong', { class: 'feedback-title', text: label }));
      if (latest?.response) panel.append(el('p', { class: 'small-text muted', text: `你的作答：${latest.response}` }));
      if (feedback.reason || feedback.message) panel.append(markdown(feedback.reason || feedback.message));
      if (reviewing) panel.append(el('p', { class: 'small-text muted', text: '开放题需要结合解题过程评阅。参考解析可以直接查看，系统不会用模糊匹配给出虚假的精确分数。' }));
      const reference = full.answer ?? full.reference_answer ?? feedback.reference_answer ?? feedback.answer; const provenance = answerProvenance(full);
      if (full.original_reference_answer != null && plain(full.original_reference_answer) && plain(full.original_reference_answer) !== plain(reference)) {
        const originalFromSource = full.original_reference_origin === 'source' || full.original_answer_origin === 'source' || !!(full.answer_source || full.answer_source_id);
        panel.append(el('div', { class: 'inline-row section-spacer' }, el('h4', { text: originalFromSource ? '资料提供的原始答案' : '原先的参考答案' }), badge(originalFromSource ? '资料提供 · 原样留存' : '历史版本 · 原样留存', originalFromSource ? 'teal' : 'outlined')), markdown(full.original_reference_answer));
      }
      if (reference != null && plain(reference)) {
        panel.append(el('div', { class: 'inline-row section-spacer' }, el('h4', { text: full.original_reference_answer && !provenance.source ? '本次核对参考答案' : '参考答案' }), badge(provenance.label, provenance.color)), markdown(reference));
        if (full.answer_source) panel.append(refs([full.answer_source]));
      }
      if (list(full.answer_source_refs).length) panel.append(el('h4', { class: 'section-spacer', text: full.answer_basis === 'source_answer' ? '资料答案出处' : '答案依据 · 本课程资料' }), refs(full.answer_source_refs));
      if (full.answer_reference_precision === 'chapter') panel.append(notice('当前为章节级资料出处，具体知识位置仍需核对。', 'info'));
      if (['needs_support', 'pending_generation'].includes(full.answer_status) || full.answer_basis === 'insufficient_material') panel.append(notice('资料尚未给出答案或依据不足，暂不把本题判为掌握。Agent 应先依据本课程资料补全并标注出处。', 'warning'));
      if (full.explanation || feedback.explanation) panel.append(el('div', { class: 'inline-row section-spacer' }, el('h4', { text: '解题思路' }), badge(full.explanation_origin === 'source' ? '资料提供' : 'Agent 整理', full.explanation_origin === 'source' ? 'teal' : 'outlined')), markdown(full.explanation || feedback.explanation));
      if (full.answer_status === 'needs_review' && full.candidate_answer != null) panel.append(el('details', { class: 'candidate-answer section-spacer' }, el('summary', { text: '查看 Agent 候选疑点（待核对，尚未采纳）' }), el('div', { class: 'solution' }, notice('资料答案仍优先保留。以下仅为 Agent 提出的候选答案，不是已确认的正确答案。', 'warning'), markdown(full.candidate_answer), full.candidate_explanation && markdown(full.candidate_explanation))));
      if (!reference && !full.explanation) panel.append(el('p', { class: 'small-text muted', text: '现有资料中尚无可核对的参考解析。这次作答会保留，可在答疑任务中请 Agent 评阅。' }));
      panel.append(answerChallengeCard(full), answerVersionCard(full));
      card.append(panel);
    }
    const pagination = el('div', { class: 'inline-row space-between section-spacer' }, button('← 上一题', () => { scope.index = Math.max(0, state.questionIndex - 1); render(); }, 'secondary small'), el('span', { class: 'subtle', text: `练习 ${state.questionIndex + 1} / ${filtered.length}` }), button('下一题 →', () => { scope.index = Math.min(filtered.length - 1, state.questionIndex + 1); render(); }, 'secondary small'));
    pagination.firstChild.disabled = state.questionIndex === 0; pagination.lastChild.disabled = state.questionIndex === filtered.length - 1;
    page.append(card, pagination); return page;
  }

  function recordsPage() {
    const attempts = list(state.detail.attempts).slice().reverse(); const read = chapters().filter(c => hasRead(c.id)); const responses = list(state.detail.responses).slice().reverse(); const notes = list(state.detail.notes).slice().reverse();
    const history = el('section', { class: 'card' }, section('练习与学习记录', '你做过的尝试、看过的解析，都有迹可循'));
    if (attempts.length || read.length) {
      read.forEach(chapter => history.append(el('div', { class: 'record-row' }, el('span', { class: 'record-dot' }), el('div', { class: 'record-body' }, el('h4', { text: chapter.title }), el('p', { text: progressLabels[progressFor(chapter.id).status] || '已读' })), el('time', { text: dateLabel(progressFor(chapter.id).at) }))));
      attempts.slice(0, 30).forEach(attempt => { const question = allQuestions().find(q => q.id === attempt.question_id); const result = attempt.result || attempt.status; const title = ({ right: '答案核对通过', correct: '答案核对通过', wrong: '需要回看与练习', incorrect: '需要回看与练习', unsure: '开放题 · 等待评阅', review_required: '开放题 · 等待评阅' }[result] || '作答已保存'); history.append(el('div', { class: 'record-row' }, el('span', { class: 'record-dot' }), el('div', { class: 'record-body' }, el('h4', { text: title }), el('p', { text: plain(question?.question || attempt.question_id).slice(0, 110) }), el('p', { text: `你的作答：${plain(attempt.response).slice(0, 160)}` }), badge(attempt.independent ? '独立作答' : '借助提示 / 看过解析', attempt.independent ? 'teal' : '')), el('time', { text: dateLabel(attempt.at || attempt.created_at) }))); });
    } else history.append(empty('第一条记录，来自下一次行动', '标记章节已读、完成一道题或提出一个问题，都会为下一轮复习留下依据。', button('去学习章节', () => navigate('chapters'), 'ghost small'), 'records'));
    const exportCard = el('section', { class: 'card' }, section('带走这一轮的收获', '汇总重点、讲解、练习和学习记录'), el('p', { class: 'small-text muted', text: '导出当前课程的复习内容，保留来源说明和掌握状态，方便考前查阅或离线留存。' }), el('div', { class: 'section-spacer' }, button('导出课程复习包', e => busy(e.currentTarget, async () => { state.exportResult = await api('/api/export', { course_id: state.courseId, package_id: currentBundle()?.manifest?.id }); toast('复习资料已经导出。'); render(); }), 'primary', 'download')));
    if (state.exportResult) {
      const output = state.exportResult; const files = output.files || output.file_paths || [];
      exportCard.append(el('div', { class: 'export-result section-spacer' }, el('strong', { text: '导出完成' }), el('p', { class: 'section-spacer', text: output.directory || '文件已保存到本地课程空间' }), el('ul', {}, ...(Array.isArray(files) ? files : Object.keys(files)).map(file => el('li', { text: typeof file === 'string' ? file : file.name || file.path || plain(file) }))), output.download_url && button('下载压缩包', e => busy(e.currentTarget, () => downloadExport(output.download_url)), 'secondary small', 'download')));
    }
    const answerCard = el('section', { class: 'card notes-list' }, section('已完成的答疑', `${responses.length} 条回答已留存在这门课里`));
    if (responses.length) answerCard.append(...responses.slice(0, 12).map(response => el('details', { class: 'section-spacer' }, el('summary', { text: response.request || (response.kind === 'review' ? '开放题作答评阅' : '课程答疑') }), el('div', { class: 'solution' }, markdown(response.text || response.answer || response.response), el('p', { class: 'subtle', text: dateLabel(response.at) })))));
    else answerCard.append(el('p', { class: 'small-text muted', text: '你在章节中提出的问题和开放题评阅结果，会在这里留存。' }));
    const noteCard = el('section', { class: 'card notes-list' }, section('学习笔记', '简短记录，也能帮下一次更快进入状态'));
    if (notes.length) noteCard.append(...notes.slice(0, 10).map(note => el('div', { class: 'record-row' }, el('span', { class: 'record-dot' }), el('div', { class: 'record-body' }, markdown(typeof note === 'string' ? note : note.text || note.content || note.summary || note.note || note.title || '学习记录')), typeof note === 'object' && el('time', { text: dateLabel(note.at || note.created_at) }))));
    else noteCard.append(el('p', { class: 'small-text muted', text: 'Agent 整理的学习笔记和复习反馈会显示在这里。' }));
    return el('div', {}, heading('复习过的，都留下来', '这里记录的是学习过程，方便下一次从真正需要的地方继续。'), el('div', { class: 'grid-two' }, el('div', { class: 'stack' }, history, answerCard), el('div', { class: 'stack' }, exportCard, noteCard)));
  }
  async function downloadExport(path) {
    const parsed = new URL(path, location.origin); if (parsed.origin !== location.origin) throw new Error('下载地址不属于当前本地工作台。');
    const response = await fetch(parsed.href, { headers: { 'X-Session-Token': state.token }, credentials: 'same-origin' }); if (!response.ok) throw new Error('压缩包下载未完成，请重新导出后再试。');
    const link = document.createElement('a'); const blob = URL.createObjectURL(await response.blob()); link.href = blob; link.download = `${currentCourse().name || '课程'}_复习资料.zip`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(blob), 10000);
  }
  async function searchCourse(query) {
    if (!query.trim() || !state.courseId) return;
    const expectedId = state.courseId; const result = await api(url('/api/search', { q: query.trim(), package_id: currentBundle()?.manifest?.id }));
    if (state.courseId !== expectedId) return; state.search = query.trim(); state.searchResults = list(result.results || result); render();
  }
  function searchPage() {
    const results = state.searchResults || [];
    return el('div', {}, heading(`搜索「${state.search}」`, `在这门课程中找到 ${results.length} 条相关内容`, [button('返回课程', () => navigate(state.page), 'secondary small')], 'FIND IT IN YOUR COURSE'), results.length ? el('div', { class: 'search-results' }, ...results.map(result => el('button', { class: 'search-result', type: 'button', onclick: () => {
      if (result.chapter_id) navigate('chapters', result.chapter_id);
      else if (result.type === 'focus' || result.kind === 'focus') navigate('focus');
      else if (result.question_id || result.type === 'question') { const question = allQuestions().find(q => q.id === result.question_id || q.id === result.id); state.practiceOrigin = question ? questionOriginKey(question) : 'provided'; const scope = state.practiceScopes[state.practiceOrigin]; scope.chapter = 'all'; scope.mistakesOnly = false; scope.index = Math.max(0, allQuestions().filter(q => questionOriginKey(q) === state.practiceOrigin).findIndex(q => q.id === question?.id)); navigate('practice'); }
      else navigate('chapters');
    } }, el('h3', { text: result.title || result.chapter_title || result.filename || '相关课程内容' }), el('p', { text: result.snippet || result.excerpt || result.text || result.content || '' }), el('small', { text: [result.type || result.kind, result.source || result.filename, result.page && `第 ${result.page} 页`].filter(Boolean).join(' · ') })))) : el('div', { class: 'card' }, empty('暂时没有找到匹配内容', '试试更短的知识点名称，或确认相关资料已经导入并完成整理。', null, 'search')));
  }

  function openConsentDialog(job) {
    if (!job?.id) return;
    state.consentJob = job;
    const scope = job.authorization || {};
    $('#consent-error').textContent = '';
    $('#consent-scope').replaceChildren(el('strong', { text: scope.course_name || currentCourse().name }), el('p', { text: `本次选中 ${scope.source_count ?? list(job.input_sources).length} 份资料及整理所需的学习记录。` }), el('ul', {}, ...list(scope.source_names || list(job.input_sources).map(source => source.filename)).map(name => el('li', { text: typeof name === 'string' ? name : plain(name) }))));
    if (!$('#consent-dialog').open) $('#consent-dialog').showModal();
  }
  function maybeOpenConsent(job) { if (job?.authorization_required) openConsentDialog(job); }
  function openRevokeConsentDialog() {
    state.revokeCourseId = state.courseId;
    $('#revoke-consent-error').textContent = '';
    $('#revoke-consent-description').textContent = `撤销「${currentCourse().name}」的资料处理授权。已有本地成果保留，正在运行的后续模型处理会停止。`;
    $('#revoke-consent-dialog').showModal();
  }
  $('#consent-later').addEventListener('click', () => { $('#consent-dialog').close(); state.consentJob = null; });
  $('#consent-dialog').addEventListener('cancel', () => { state.consentJob = null; });
  $('#consent-allow').addEventListener('click', async event => {
    const job = state.consentJob; if (!job) return;
    event.currentTarget.disabled = true;
    try { await api('/api/consent', { course_id: job.course_id || state.courseId, job_id: job.id, allow: true }); $('#consent-dialog').close(); state.consentJob = null; toast('已允许处理本课程这些资料，自动 Agent 可以接单。'); await refreshCourse(); }
    catch (error) { $('#consent-error').textContent = error.message || plain(error); }
    finally { event.currentTarget.disabled = false; }
  });
  $('#revoke-consent-cancel').addEventListener('click', () => $('#revoke-consent-dialog').close());
  $('#revoke-consent-confirm').addEventListener('click', async event => {
    event.currentTarget.disabled = true;
    try { await api('/api/consent', { course_id: state.revokeCourseId, allow: false }); $('#revoke-consent-dialog').close(); toast('本课程授权已撤销，已生成资料继续保留。'); await refreshCourse(); }
    catch (error) { $('#revoke-consent-error').textContent = error.message || plain(error); }
    finally { event.currentTarget.disabled = false; }
  });

  function openCourseDialog() { $('#course-form-error').textContent = ''; $('#course-dialog').showModal(); setTimeout(() => $('#new-course-name').focus(), 50); }
  $('#new-course').addEventListener('click', openCourseDialog);
  $('#close-dialog').addEventListener('click', () => $('#course-dialog').close());
  $('#cancel-course').addEventListener('click', () => $('#course-dialog').close());
  $('#course-dialog').addEventListener('click', e => { if (e.target === $('#course-dialog')) { const rect = e.target.getBoundingClientRect(); if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) e.target.close(); } });
  $('#course-form').addEventListener('submit', async e => {
    e.preventDefault(); const form = e.currentTarget; const data = new FormData(form); const submit = form.querySelector('[type=submit]');
    if (+data.get('passing_score') > +data.get('score_max')) { $('#course-form-error').textContent = '及格线不能超过卷面满分。'; return; }
    submit.disabled = true;
    try {
      const created = await api('/api/courses', { name: data.get('name').trim(), mode: data.get('mode'), score_max: +data.get('score_max'), passing_score: +data.get('passing_score') });
      const course = created.course || created; state.courses = list(await api('/api/courses')); $('#course-dialog').close(); form.reset(); state.page = 'materials'; await chooseCourse(course.id); toast('课程已创建，可以添加学习资料了。');
    } catch (error) { $('#course-form-error').textContent = error.message || plain(error); } finally { submit.disabled = false; }
  });
  $('#course-select').addEventListener('change', e => chooseCourse(e.target.value).catch(report));
  $('#refresh-button').addEventListener('click', e => busy(e.currentTarget, () => refreshCourse()));
  $('#search-form').addEventListener('submit', e => { e.preventDefault(); searchCourse($('#global-search').value).catch(report); });
  $('.brand').addEventListener('click', e => { e.preventDefault(); navigate('overview'); });
  window.addEventListener('focus', () => { if (state.detail) refreshCourse({ quiet: true }); });

  async function initialize() {
    if (!state.token) { render(); $('#connection-state').textContent = '需要从本地启动器重新打开'; return; }
    try {
      const results = await Promise.allSettled([api('/api/courses'), api('/api/defaults')]);
      if (results[0].status === 'rejected') throw results[0].reason;
      state.courses = list(results[0].value);
      if (results[1].status === 'fulfilled') state.defaults = results[1].value || {};
      state.materialPath = state.defaults.material_directory || '';
      if (!state.courses.some(c => c.id === state.courseId && !c.error)) state.courseId = state.defaults.course_id || state.courses.find(c => !c.error)?.id || '';
      $('#connection-state').textContent = '已连接 · 学习记录保存在本地';
      if (state.courseId) await chooseCourse(state.courseId); else render();
    } catch (error) {
      $('#connection-state').textContent = '本地服务暂时未连接';
      $('#main').replaceChildren(el('div', { class: 'no-auth card' }, empty('学习空间暂时没有连接上', error.message || '请确认本地启动器仍在运行，然后重新连接。', button('重新连接', initialize, 'primary'))));
    }
  }
  initialize();
})();
