(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const colors = {yellow: '黄色', rose: '粉色', green: '绿色', blue: '蓝色', purple: '紫色'};
  const state = {documents: [], trash: [], current: null, messages: [], notes: [], selection: null, active: null, filter: '', search: '', mode: 'paste', managing: false, selected: new Set(), undo: null};
  const drafts = new Map(), timers = new Map(), jobs = new Map(), conflicts = new Map(), sourceViews = new Set();
  let editing = null, bodyJob = null;
  let toastTimer, markTimer, busy = 0, syncing = false, mutating = false;
  const preferences = {
    get(key) {try {return localStorage.getItem('conversation-notes.' + key);} catch {return null;}},
    set(key, value) {try {localStorage.setItem('conversation-notes.' + key, value);} catch { /* UI preferences are optional. */ }}
  };
  const make = (tag, className = '', value = '') => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = value;
    return element;
  };
  const safe = fn => (...args) => Promise.resolve().then(() => fn(...args)).catch(error => toast(error.message));
  const styles = {
    codex: {name: '简约 · Codex', description: '黑白中性，专注内容'},
    paper: {name: '纸页', description: '暖色纸感，适合长文'},
    mist: {name: '雾蓝', description: '清爽蓝调，轻盈安静'}
  };
  const colorPreference = window.matchMedia('(prefers-color-scheme: dark)');
  let appearance = {style: 'codex', mode: 'auto'}, appearanceSequence = 0, appearancePending = false;
  let appearanceJob = Promise.resolve(true);
  const validAppearance = value => value && Object.hasOwn(styles, value.style) && ['auto', 'light', 'dark'].includes(value.mode);
  try {
    const cached = JSON.parse(preferences.get('appearance'));
    if (validAppearance(cached)) {appearance = {style: cached.style, mode: cached.mode}; appearancePending = !!cached.pending;}
  } catch { /* Start with the default if an older cache is unavailable. */ }

  function applyAppearance() {
    const host = document.documentElement.dataset.hostTheme;
    const scheme = appearance.mode === 'auto' ? (['light', 'dark'].includes(host) ? host : colorPreference.matches ? 'dark' : 'light') : appearance.mode;
    document.documentElement.dataset.style = appearance.style;
    document.documentElement.dataset.scheme = scheme;
    $$('[data-reader-style]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.readerStyle === appearance.style)));
    $$('[data-color-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.colorMode === appearance.mode)));
    const auto = $('[data-color-mode="auto"]');
    auto.textContent = host ? '跟随 Codex' : '跟随系统';
    const modeName = {light: '浅色', dark: '深色'}[scheme];
    $('#appearance-current').textContent = styles[appearance.style].name + ' · ' + (appearance.mode === 'auto' ? `${host ? '跟随 Codex' : '跟随系统'}（${modeName}）` : modeName);
  }
  function renderStyleOptions() {
    const root = $('#style-options');
    for (const [id, style] of Object.entries(styles)) {
      const button = make('button', 'style-option'); button.dataset.style = id; button.dataset.readerStyle = id;
      button.setAttribute('aria-label', '选择' + style.name + '风格'); button.setAttribute('aria-pressed', 'false');
      const preview = make('span', 'style-preview'); preview.setAttribute('aria-hidden', 'true');
      const top = make('span', 'style-preview-top'); top.append(make('i'), make('i'), make('i'), make('b'));
      const content = make('span', 'style-preview-content'), chat = make('span', 'style-preview-chat');
      chat.append(make('b'), make('i'), make('i'));
      const bubble = make('span', 'style-preview-bubble'); bubble.append(make('i')); chat.append(bubble);
      const aside = make('span', 'style-preview-aside'), note = make('span', 'style-preview-note');
      note.append(make('i'), make('i'), make('i')); aside.append(note); content.append(chat, aside); preview.append(top, content);
      const caption = make('span', 'style-caption'); caption.append(make('span', 'style-title', style.name), make('span', 'style-description', style.description), make('span', 'style-check', '✓'));
      button.append(preview, caption); button.onclick = () => changeAppearance({style: id}); root.append(button);
    }
  }
  function rememberAppearance() {
    const chosen = {...appearance}, sequence = ++appearanceSequence;
    appearancePending = true; preferences.set('appearance', JSON.stringify({...chosen, pending: true}));
    const status = $('#appearance-save-state'); status.textContent = '保存中…'; status.classList.remove('error');
    appearanceJob = appearanceJob.catch(() => false).then(async () => {
      try {
        await bridge.call('set_appearance', chosen);
        if (sequence === appearanceSequence) {
          appearancePending = false; preferences.set('appearance', JSON.stringify(chosen));
          status.textContent = '✓ 已记住'; status.classList.remove('error');
        }
        return true;
      } catch (error) {
        if (sequence === appearanceSequence) {status.textContent = '保存失败 · 点击重试'; status.classList.add('error'); toast('外观暂未保存到本机，点击外观面板的重试按钮可继续保存。');}
        return false;
      }
    });
    return appearanceJob;
  }
  function changeAppearance(patch) {
    appearance = {...appearance, ...patch}; applyAppearance(); rememberAppearance();
  }
  async function loadAppearance() {
    if (appearancePending) {await rememberAppearance(); return;}
    const sequence = appearanceSequence;
    try {
      const data = await bridge.call('get_appearance');
      if (sequence === appearanceSequence && validAppearance(data.appearance)) {
        appearance = data.appearance; preferences.set('appearance', JSON.stringify(appearance)); applyAppearance();
      }
    } catch { /* A cached choice remains usable while the server reconnects. */ }
  }
  renderStyleOptions(); applyAppearance();
  colorPreference.addEventListener('change', applyAppearance);

  class Bridge {
    constructor() {
      this.embedded = window.parent !== window;
      this.pending = new Map(); this.seq = 0; this.capabilities = {}; this.context = {};
      this.localToken = window.__NOTES_LOCAL_TOKEN__;
      this.initialData = null;
      window.addEventListener('message', event => {
        if (event.source !== window.parent || !event.data || event.data.jsonrpc !== '2.0') return;
        const message = event.data;
        if (this.pending.has(message.id)) {
          const promise = this.pending.get(message.id); this.pending.delete(message.id);
          clearTimeout(promise.timer);
          if (message.error) promise.reject(new Error(message.error.message || '连接失败'));
          else promise.resolve(message.result);
        } else if (message.method === 'ui/notifications/tool-result') {
          const data = message.params?.structuredContent;
          if (data?.documents) {
            this.initialData = data;
            window.dispatchEvent(new CustomEvent('notes-initial-data', {detail: data}));
          }
        } else if (message.method === 'ui/notifications/host-context-changed') {
          this.applyContext({...this.context, ...message.params});
        } else if (message.method === 'ui/resource-teardown') {
          Promise.allSettled([flushAll(), appearanceJob]).then(() => window.parent.postMessage({jsonrpc: '2.0', id: message.id, result: {}}, '*'));
        }
      });
    }
    request(method, params = {}, timeoutMs = 15000) {
      const id = ++this.seq;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {this.pending.delete(id); reject(new Error('连接超时，文字仍保留在面板中，请重试。'));}, timeoutMs);
        this.pending.set(id, {resolve, reject, timer});
        window.parent.postMessage({jsonrpc: '2.0', id, method, params}, '*');
      });
    }
    applyContext(context) {
      this.context = context;
      if (context.theme) document.documentElement.dataset.hostTheme = context.theme;
      applyAppearance();
      for (const [key, value] of Object.entries(context.styles?.variables || {})) {
        if (key.startsWith('--') && typeof value === 'string') document.documentElement.style.setProperty(key, value);
      }
      for (const [key, value] of Object.entries(context.safeAreaInsets || {})) {
        document.body.style.setProperty(`--safe-${key}`, `${Number(value) || 0}px`);
      }
    }
    async connect() {
      if (!this.embedded) return;
      const result = await this.request('ui/initialize', {
        protocolVersion: '2026-01-26',
        appInfo: {name: 'conversation-notes', version: '0.6.1'},
        appCapabilities: {availableDisplayModes: ['inline', 'fullscreen']}
      });
      this.capabilities = result.hostCapabilities || {};
      this.hostInfo = result.hostInfo || {};
      this.applyContext(result.hostContext || {});
      window.parent.postMessage({jsonrpc: '2.0', method: 'ui/notifications/initialized'}, '*');
    }
    async call(name, args = {}) {
      if (!this.embedded) {
        const response = await fetch('/api/tool', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Notes-Token': this.localToken}, body: JSON.stringify({name, arguments: args})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '保存失败，请重试');
        return data;
      }
      const response = await this.request('tools/call', {name, arguments: args}, name === 'export_notes' ? 225000 : 15000);
      if (response.isError) throw new Error(response.content?.find(c => c.type === 'text')?.text || '操作失败');
      if (response._meta?.download) return response._meta.download;
      if (response.structuredContent) return response.structuredContent;
      return JSON.parse(response.content.find(c => c.type === 'text').text);
    }
    async openChat(threadId) {
      const url = 'codex://threads/' + encodeURIComponent(threadId);
      if (this.embedded) return this.request('ui/open-link', {url});
      const anchor = make('a'); anchor.href = url; document.body.append(anchor); anchor.click(); anchor.remove();
    }
    async openURL(url) {
      if (!/^(https?:|codex:|#)/i.test(url)) return;
      if (url.startsWith('#')) {document.getElementById(url.slice(1))?.scrollIntoView({block:'center'}); return;}
      if (this.embedded) return this.request('ui/open-link', {url});
      window.open(url, '_blank', 'noopener,noreferrer');
    }
    async download(data) {
      if (this.embedded && this.capabilities.downloadFile) {
        const resource = {uri: 'file:///' + encodeURIComponent(data.filename), mimeType: data.mime};
        if (data.blob) resource.blob = data.blob; else resource.text = data.content;
        return this.request('ui/download-file', {contents: [{type: 'resource', resource}]});
      }
      const content = data.blob ? Uint8Array.from(atob(data.blob), c => c.charCodeAt(0)) : data.content;
      // The in-app browser otherwise previews PDF blobs instead of saving them.
      const downloadMime = data.mime === 'application/pdf' ? 'application/octet-stream' : data.mime;
      const url = URL.createObjectURL(new Blob([content], {type: downloadMime}));
      const anchor = make('a'); anchor.href = url; anchor.download = data.filename;
      document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
  }
  const bridge = new Bridge();

  function toast(message, action) {
    const box = $('#toast'); box.replaceChildren(make('span', '', message));
    if (action) {const b = make('button', '', action.label); b.onclick = safe(action.run); box.append(b);}
    box.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => box.hidden = true, action ? 12000 : 5500);
  }
  function saveStatus(error = false) {
    const element = $('#save-state'); element.classList.toggle('error', error);
    element.textContent = error ? '保存失败 · 请重试' : busy ? '保存中…' : editing?.draft!==editing?.original ? '正文待保存' : drafts.size ? '正在保存…' : '✓ 已保存到本机';
  }
  function noteStatus(id, message, error = false) {
    const node = document.getElementById('note-save-' + id);
    if (node) {node.textContent = message; node.classList.toggle('error', error);}
  }
  function setDraft(id, patch, delay = 650) {
    drafts.set(id, {...drafts.get(id), ...patch});
    clearTimeout(timers.get(id));
    timers.set(id, setTimeout(() => saveNote(id), delay));
    noteStatus(id, '待保存'); saveStatus();
  }
  async function saveNote(id) {
    clearTimeout(timers.get(id)); timers.delete(id);
    if (jobs.has(id)) {await jobs.get(id); if (drafts.has(id)) return saveNote(id); return true;}
    const patch = drafts.get(id); if (!patch) return true;
    if (conflicts.has(id)) {noteStatus(id, '存在冲突 · 点击处理', true); return false;}
    const note = state.notes.find(n => n.id === id); if (!note) return false;
    drafts.delete(id); busy++; saveStatus(); noteStatus(id, '保存中…');
    const job = (async () => {
      try {
        const data = await bridge.call('update_annotation', {annotation_id: id, expected_version: note.version, ...patch});
        Object.assign(note, data.annotation); noteStatus(id, '已保存'); return true;
      } catch (error) {
        drafts.set(id, {...patch, ...drafts.get(id)});
        if (error.message.includes('其他面板')) {
          const latest = await bridge.call('get_document', {document_id: state.current.id}).catch(() => null);
          const current = latest?.annotations.find(n => n.id === id);
          conflicts.set(id, current || null);
          noteStatus(id, '存在冲突 · 点击处理', true);
          toast('另一面板已更新这条批注。你的文字仍保留，点击批注的冲突提示可处理。');
        } else {noteStatus(id, '保存失败 · 点击重试', true); toast(error.message);}
        return false;
      } finally {busy--; saveStatus(drafts.has(id) && !timers.has(id));}
    })();
    jobs.set(id, job);
    const result = await job; jobs.delete(id); return result;
  }
  async function retryNote(id) {
    if (!conflicts.has(id)) return saveNote(id);
    const current = conflicts.get(id);
    if (!current) {toast('这条批注可能已被移除。请先复制你的文字，再重新打开这份原文。'); return false;}
    toast('确认后，将把你当前填写的内容保存到这条批注。其他未编辑字段会保留。', {
      label: '保留我的修改', run: async () => {
        Object.assign(state.notes.find(n => n.id === id), current);
        conflicts.delete(id);
        const ok = await saveNote(id);
        if (ok) {renderReader(); renderNotes(); toast('你的修改已保存');}
      }
    });
    return false;
  }
  async function flushAll() {
    const ids = new Set([...drafts.keys(), ...jobs.keys()]);
    const results = await Promise.all([...ids].map(saveNote));
    return results.every(Boolean) && await saveBody();
  }
  function richOptions(message, notes = []) {
    return {messageId:message.id, notes, active:state.active, attachments:message.attachments || [],
      image:reference=>bridge.call('get_message_image',{message_id:message.id,reference}),
      download:data=>bridge.download(data),open:safe(url=>bridge.openURL(url)),toast,
      select:(start,end,label)=>{state.selection={message_id:message.id,start,end,quote:message.body.slice(start,end)};$('#selection-label').textContent=label;$('#selection-bar').hidden=false;}};
  }
  function cacheBody() {
    preferences.set('body-draft', editing && editing.draft !== editing.original ? JSON.stringify({id:editing.id,version:editing.version,draft:editing.draft,document_id:state.current.id}) : '');
  }
  function recoverBody() {
    if (editing) return;
    try {
      const draft = JSON.parse(preferences.get('body-draft'));
      const message = draft && state.messages.find(m=>m.id===draft.id);
      if (draft?.document_id===state.current?.id && message) {
        if(draft.draft===message.body){preferences.set('body-draft','');return;}
        editing={...draft,original:message.body,preview:false,error:'',saving:false};
        if(message.version!==draft.version) editing.error='消息已在其他面板变化。草稿已保留，请先复制内容再取消编辑，核对新版本。';
        toast('已恢复未保存的正文草稿');
      }
    } catch { /* A malformed optional draft must not block the library. */ }
  }
  async function beginEdit(id) {
    if (editing?.id===id) {$('.body-input')?.focus(); return;}
    if (!await flushAll()) return;
    const message=state.messages.find(m=>m.id===id);if(!message)return;
    clearTimeout(markTimer);hideSelection();window.getSelection()?.removeAllRanges();
    editing={id,version:message.version,original:message.body,draft:message.body,preview:false,error:'',saving:false};
    renderReader(); const input=$('.body-input');input?.focus();input?.scrollIntoView({block:'nearest'});
  }
  async function saveBody() {
    if(bodyJob)return bodyJob;
    if(!editing)return true;
    const draft=editing;
    if(draft.draft===draft.original){editing=null;cacheBody();renderReader();return true;}
    draft.saving=true;busy++;saveStatus();
    $$('.message-editor button,.body-input').forEach(e=>e.disabled=true);
    bodyJob=(async()=>{
      try {
        const data=await bridge.call('edit_message',{message_id:draft.id,body:draft.draft,expected_version:draft.version});
        editing=null;cacheBody();adopt(data);await library();toast('正文已保存，可用“撤销操作”恢复');return true;
      }catch(error){draft.error=error.message;draft.saving=false;renderReader();toast(error.message);return false;}
      finally{busy--;bodyJob=null;saveStatus();}
    })();
    return bodyJob;
  }
  function bodyEditor(message) {
    const draft=editing,box=make('div','message-editor'),toolbar=make('div','editor-toolbar'),input=make('textarea','body-input');
    input.value=draft.draft;input.setAttribute('aria-label','编辑消息正文');input.spellcheck=false;input.rows=Math.min(22,Math.max(7,draft.draft.split('\n').length+1));input.hidden=draft.preview;
    const preview=make('div','body-preview rich-text');preview.hidden=!draft.preview;
    function changed(){draft.draft=input.value;draft.error='';cacheBody();saveStatus();if(draft.preview)NotesRich.render(preview,draft.draft,{...richOptions(message),select:()=>{}});}
    input.oninput=changed;
    function insert(before,after='',placeholder='文字') {
      draft.preview=false;input.hidden=false;preview.hidden=true;toggle.textContent='预览';toggle.setAttribute('aria-pressed','false');
      const a=input.selectionStart,b=input.selectionEnd,value=input.value.slice(a,b)||placeholder;
      input.setRangeText(before+value+after,a,b,'select');input.setSelectionRange(a+before.length,a+before.length+value.length);changed();input.focus();
    }
    for(const [label,title,before,after,placeholder] of [
      ['B','加粗','**','**','重点'],['I','斜体','*','*','文字'],['标题','插入标题','\n## ','\n','标题'],
      ['列表','插入列表','\n- ','\n','列表项'],['代码','插入代码块','\n```\n','\n```\n','代码'],
      ['表格','插入表格','\n| 项目 | 内容 |\n| --- | --- |\n| ',' | 说明 |\n','名称'],
      ['公式','插入公式','$','$','E=mc^2'],['图片','插入图片','\n![图片](<','>)\n','图片路径或网址']]) {
      const button=make('button','quiet',label);button.title=title;button.setAttribute('aria-label',title);button.onclick=()=>insert(before,after,placeholder);toolbar.append(button);
    }
    const toggle=make('button','quiet editor-preview-toggle',draft.preview?'继续编辑':'预览');toggle.setAttribute('aria-pressed',String(draft.preview));
    toggle.onclick=()=>{draft.preview=!draft.preview;input.hidden=draft.preview;preview.hidden=!draft.preview;toggle.textContent=draft.preview?'继续编辑':'预览';toggle.setAttribute('aria-pressed',String(draft.preview));if(draft.preview)NotesRich.render(preview,draft.draft,{...richOptions(message),select:()=>{}});else input.focus();};toolbar.append(toggle);
    const error=make('p','editor-error',draft.error),footer=make('div','editor-footer'),cancel=make('button','quiet','取消'),save=make('button','primary','保存修改');
    cancel.onclick=()=>{editing=null;cacheBody();renderReader();saveStatus();};save.onclick=safe(async()=>{if(await flushAll())saveStatus();});
    footer.append(make('span','editor-help','支持 Markdown · ⌘ / Ctrl + Enter 保存'),cancel,save);
    input.onkeydown=event=>{
      if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();safe(flushAll)();}
      if(event.key.toLowerCase()==='b'&&(event.metaKey||event.ctrlKey)){event.preventDefault();insert('**','**','重点');}
      if(event.key==='Tab'){event.preventDefault();input.setRangeText('  ',input.selectionStart,input.selectionEnd,'end');changed();}
      if(event.key==='Escape'){event.preventDefault();editing=null;cacheBody();renderReader();saveStatus();}
    };
    box.append(toolbar,input,preview,error,footer);
    if(draft.preview)NotesRich.render(preview,draft.draft,{...richOptions(message),select:()=>{}});
    return box;
  }
  async function library() {
    const data = await bridge.call('list_documents'); state.documents = data.documents; state.trash = data.trash || []; renderDocumentSelect(); renderEditControls();
  }
  function renderDocumentSelect() {
    const select = $('#document-select'); select.replaceChildren();
    if (!state.documents.length) {const option = make('option', '', '还没有原文'); option.value = ''; select.append(option);}
    for (const d of state.documents) {const option = make('option', '', d.title); option.value = d.id; select.append(option);}
    if (state.current) select.value = state.current.id;
  }
  async function loadDocument(id) {
    if (!await flushAll()) {renderDocumentSelect(); return;}
    let data = await bridge.call('get_document', {document_id: id});
    if (data.document.source_kind === 'local-chat') {
      try {data = await bridge.call('import_thread', {thread_id: data.document.thread_id, title: data.document.title});}
      catch (error) {toast('暂时无法同步，已打开保存的副本。' + error.message);}
    }
    adopt(data, 'latest');
  }
  function viewPosition() {
    const box = $('#reader-scroll'), top = box.getBoundingClientRect().top;
    const anchor = $$('.message').find(m => m.getBoundingClientRect().bottom > top);
    return {top: box.scrollTop, bottom: box.scrollHeight - box.scrollTop - box.clientHeight < 90,
      id: anchor?.dataset.messageId, delta: anchor ? anchor.getBoundingClientRect().top - top : 0};
  }
  function restorePosition(view) {
    const box = $('#reader-scroll');
    const anchor = $$('.message').find(m => m.dataset.messageId === view.id);
    box.scrollTop = anchor ? box.scrollTop + anchor.getBoundingClientRect().top - box.getBoundingClientRect().top - view.delta : view.top;
  }
  function latest() {const box = $('#reader-scroll'); box.scrollTop = box.scrollHeight; $('#latest-button').textContent = '最新消息 ↓';}
  function adopt(data, position = 'preserve') {
    const same = state.current?.id === data.document?.id, view = viewPosition();
    state.current = data.document; state.messages = data.messages; state.notes = data.annotations;
    recoverBody();
    state.undo = data.undo || null; state.selected.clear();
    state.selection = null; state.active = null; $('#selection-bar').hidden = true;
    $('#source-button').hidden = !state.current?.thread_id;
    $('#sync-button').hidden = state.current?.source_kind !== 'local-chat';
    if (data.documents) state.documents = data.documents;
    if (data.trash) state.trash = data.trash;
    renderDocumentSelect(); renderReader(); renderNotes(); saveStatus();
    renderEditControls();
    if (state.current) preferences.set('document', state.current.id);
    if (position === 'latest' || (!same && state.current?.source_kind === 'local-chat')) latest();
    else if (same) restorePosition(view);
    else $('#reader-scroll').scrollTop = 0;
    if(editing) $('.message-editor')?.scrollIntoView({block:'nearest'});
  }
  function empty(root, title, description, small = false, action) {
    const box = make('div', 'empty' + (small ? ' small-empty' : ''));
    box.append(make('div', 'empty-glyph', small ? '✎' : '﹏'), make('h2', '', title), make('p', '', description));
    if (action) {const button = make('button', 'primary', action.label); button.onclick = action.run; box.append(button);}
    root.append(box);
  }
  function renderReader() {
    const view = viewPosition();
    const root = $('#reader'); root.replaceChildren();
    if (!state.current) {
      $('#document-summary').textContent = '';
      empty(root, '为对话留一页笔记', '加入一段值得记住的原文，划出重点，在旁边写下你的想法。', false, {label: '＋ 加入第一段原文', run: openImport}); return;
    }
    root.append(make('h2', 'document-title', state.current.title));
    root.append(make('div', 'document-meta', `${state.messages.length} 段原文 · ${state.notes.length} 处重点`));
    for (const [index, message] of state.messages.entries()) {
      const article = make('article', 'message ' + message.role); article.dataset.messageId = message.id;
      const heading = make('div', 'message-heading'); heading.append(make('span', '', {user: '你', assistant: 'CODEX', excerpt: '原文摘录'}[message.role] || '原文'), make('span', '', String(index + 1).padStart(2, '0')));
      if (state.managing) {
        const check = make('input', 'message-check'); check.type = 'checkbox'; check.checked = state.selected.has(message.id);
        check.setAttribute('aria-label', `选择第 ${index + 1} 段消息`);
        check.onchange = () => {if (check.checked) state.selected.add(message.id); else state.selected.delete(message.id); renderEditControls();};
        heading.prepend(check);
      }
      if(message.edited)heading.append(make('span','edited-label','已编辑'));
      const actions=make('div','message-actions'),edit=make('button','quiet','编辑'),source=make('button','quiet',sourceViews.has(message.id)?'排版':'源码');
      edit.setAttribute('aria-label',`编辑第 ${index+1} 段消息`);edit.onclick=safe(()=>beginEdit(message.id));
      source.setAttribute('aria-label',`切换第 ${index+1} 段消息的源码或排版`);source.onclick=safe(async()=>{if(!await flushAll())return;if(sourceViews.has(message.id))sourceViews.delete(message.id);else sourceViews.add(message.id);renderReader();});
      const remove = make('button', 'quiet message-remove', '删除'); remove.setAttribute('aria-label', `删除第 ${index + 1} 段消息`);
      remove.title = '从墨记副本删除这段消息，可撤销'; remove.onclick = safe(() => removeMessages([message.id]));actions.append(edit,source,remove);heading.append(actions);
      const body = make('div', 'message-body'); body.dataset.messageId = message.id; body.tabIndex = 0;
      const notes = state.notes.filter(n => n.message_id === message.id);
      if(editing?.id===message.id)article.append(heading,bodyEditor(message));
      else {NotesRich.render(body,message.body,{...richOptions(message,notes),sourceMode:sourceViews.has(message.id)});article.append(heading,body);}
      root.append(article);
    }
    $('#document-summary').textContent = `${state.messages.length} 段原文 · ${state.notes.length} 处重点`;
    restorePosition(view);
  }
  function renderEditControls() {
    for (const id of ['manage-button', 'delete-document-button', 'latest-button','rename-button']) $('#' + id).hidden = !state.current;
    $('#manage-button').textContent = state.managing ? '完成选择' : '选择消息';
    $('#bulk-controls').hidden = !state.current || !state.managing;
    $('#delete-selected-button').disabled = !state.selected.size;
    $('#delete-selected-button').textContent = state.selected.size ? `删除选中 ${state.selected.size} 段` : '删除选中消息';
    $('#undo-button').hidden = !state.undo;
    $('#undo-button').title = state.undo ? '撤销：' + state.undo.label : '';
    $('#trash-button').textContent = '回收站' + (state.trash.length ? ` (${state.trash.length})` : '');
  }
  async function changeContent(operation) {
    if (mutating || syncing) return;
    mutating = true;
    try {
      if (!await flushAll()) return;
      const data = await operation();
      if (data.document) adopt(data);
      await library(); renderEditControls();
      return data;
    } finally {mutating = false;}
  }
  async function removeMessages(ids) {
    const data = await changeContent(() => bridge.call('delete_messages', {
      document_id: state.current.id, message_ids: ids, expected_revision: state.current.revision}));
    if (data) toast(`已删除 ${ids.length} 段消息`, {label: '撤销', run: () => undoDeletion(data.undo)});
  }
  async function removeText() {
    const selection = state.selection; if (!selection) return;
    const message = state.messages.find(m => m.id === selection.message_id);
    const deletion=sourceViews.has(message.id)?selection:{message_id:message.id,...NotesRich.deletion(message.body,selection.start,selection.end)};
    const data = await changeContent(() => bridge.call('delete_text', {...deletion, expected_version: message.version}));
    if (data) {window.getSelection()?.removeAllRanges(); toast('选中文字已删除，相关批注会随保留的原句显示', {label: '撤销', run: () => undoDeletion(data.undo)});}
  }
  async function undoDeletion(action = state.undo) {
    if (!action) return;
    const data = await changeContent(() => bridge.call('undo_delete', {action_id: action.id}));
    if (data) toast('已恢复原文和对应批注');
  }
  async function removeDocument() {
    const data = await changeContent(() => bridge.call('delete_document', {document_id: state.current.id, expected_revision: state.current.revision}));
    if (!data) return;
    state.current = null; state.messages = []; state.notes = []; state.undo = null;
    $('#source-button').hidden = true; $('#sync-button').hidden = true;
    if (state.documents.length) await loadDocument(state.documents[0].id);
    else {renderDocumentSelect(); renderReader(); renderNotes(); renderEditControls();}
    toast('这份对话已移入回收站，原聊天保留', {label: '撤销', run: () => undoDeletion(data.undo)});
  }
  async function showTrash() {
    await library(); renderEditControls();
    const root = $('#trash-list'); root.replaceChildren();
    if (!state.trash.length) root.append(make('p', 'form-help', '回收站是空的。'));
    for (const item of state.trash) {
      const row = make('div', 'trash-row'), restore = make('button', 'quiet', '恢复');
      restore.setAttribute('aria-label', '恢复 ' + item.title);
      restore.onclick = safe(async () => {
        const data = await changeContent(() => bridge.call('restore_document', {document_id: item.id}));
        if (data) {$('#trash-dialog').close(); toast('对话已恢复');}
      });
      row.append(make('span', '', item.title), restore); root.append(row);
    }
    $('#trash-dialog').showModal();
  }
  function swatch(color, action, active = false, title) {
    const button = make('button', 'swatch' + (active ? ' active' : ''));
    button.dataset.color = color; button.title = title || colors[color]; button.setAttribute('aria-label', title || colors[color]);
    button.setAttribute('aria-pressed', String(active)); button.onclick = safe(action); return button;
  }
  function renderFilters() {
    const root = $('#filter-colors'); root.replaceChildren();
    const all = make('button', 'filter-all' + (!state.filter ? ' active' : ''), '全部');
    all.onclick = () => {state.filter = ''; renderFilters(); renderNotes();}; root.append(all);
    for (const color of Object.keys(colors)) root.append(swatch(color, async () => {
      if (!await flushAll()) return;
      state.filter = state.filter === color ? '' : color; renderFilters(); renderNotes();
    }, state.filter === color, '筛选' + colors[color] + '高亮'));
  }
  function renderNotes() {
    const root = $('#notes'); root.replaceChildren();
    $('#note-count').textContent = state.notes.length; $('#mobile-count').textContent = state.notes.length;
    const term = state.search.trim().toLowerCase();
    const notes = state.notes.filter(n => (!state.filter || n.color === state.filter) && (!term || [n.quote, n.comment, ...n.tags].join(' ').toLowerCase().includes(term)));
    if (!notes.length) {
      empty(root, state.notes.length ? '没有匹配的批注' : '想法，从一处高亮开始', state.notes.length ? '换个关键词或颜色试试。' : '在左侧原文中选择一句话，再选一种颜色，或点击“写批注”。', true); return;
    }
    for (const note of notes) {
      const card = make('article', 'note-card' + (state.active === note.id ? ' active' : '')); card.id = 'note-' + note.id;
      const top = make('div', 'note-top'); top.append(make('span', 'note-date', new Date(note.created_at).toLocaleDateString('zh-CN', {month: 'long', day: 'numeric'})));
      const locate = make('button', 'quiet locate-button', '定位原句 ↖'); locate.onclick = () => locateNote(note.id); top.append(locate);
      const source=state.messages.find(m=>m.id===note.message_id)?.body || note.quote;
      const quote = make('button', 'note-quote', NotesRich.excerpt(source,note.start,note.end)); quote.dataset.color = note.color; quote.title = '在阅读面板中定位原句'; quote.onclick = () => locateNote(note.id);
      const comment = make('textarea', 'note-comment'); comment.value = drafts.get(note.id)?.comment ?? note.comment;
      comment.placeholder = '写下你的想法…'; comment.setAttribute('aria-label', '批注内容');
      comment.oninput = () => setDraft(note.id, {comment: comment.value}); comment.onblur = () => saveNote(note.id);
      comment.onkeydown = event => {if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {event.preventDefault(); saveNote(note.id);}};
      const tags = make('input', 'note-tags'); tags.value = (drafts.get(note.id)?.tags ?? note.tags).join('，');
      tags.placeholder = '＋ 标签，用逗号分隔'; tags.setAttribute('aria-label', '批注标签');
      tags.oninput = () => setDraft(note.id, {tags: tags.value.split(/[,，]/).map(t => t.trim()).filter(Boolean)});
      tags.onblur = () => saveNote(note.id);
      const bottom = make('div', 'note-bottom'), palette = make('div', 'swatches');
      for (const color of Object.keys(colors)) palette.append(swatch(color, async () => {
        setDraft(note.id, {color}, 100000); const ok = await saveNote(note.id);
        if (ok) {renderReader(); renderNotes();}
      }, note.color === color, '改为' + colors[color] + '高亮'));
      const status = make('button', 'quiet note-save', drafts.has(note.id) ? '待保存' : '已保存'); status.id = 'note-save-' + note.id;
      status.onclick = safe(() => retryNote(note.id));
      const remove = make('button', 'icon-button', '×'); remove.title = '移除高亮和批注（可撤销）'; remove.setAttribute('aria-label', '移除这条批注'); remove.onclick = safe(() => deleteNote(note.id));
      bottom.append(palette, status, remove); card.append(top, quote, comment, tags, bottom); root.append(card);
    }
  }
  function captureSelection() {
    const selection = window.getSelection(); if (!selection || selection.isCollapsed || !selection.rangeCount) {hideSelection(); return;}
    const range = selection.getRangeAt(0);
    const container = node => (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement)?.closest('.message-body');
    const body = container(range.startContainer);
    if (!body || container(range.endContainer) !== body) {hideSelection(); return;}
    const message=state.messages.find(m=>m.id===body.dataset.messageId),quote=range.toString();if(!message||!quote.trim())return;
    const mapped=NotesRich.selection(body,range,message.body);if(!mapped){hideSelection();return;}
    state.selection = {message_id: message.id,...mapped};
    $('#selection-label').textContent = `已选 ${Array.from(quote).length} 字`;
    $('#selection-bar').hidden = false;
  }
  function hideSelection() {state.selection = null; $('#selection-bar').hidden = true;}
  async function addNote(color = 'yellow', focus = false) {
    if (!state.selection) return;
    if (!await flushAll()) return;
    busy++; saveStatus();
    try {
      const message = state.messages.find(m => m.id === state.selection.message_id);
      const data = await bridge.call('add_annotation', {...state.selection, expected_version: message.version, color});
      state.notes.push(data.annotation); state.active = data.annotation.id;
      hideSelection(); window.getSelection()?.removeAllRanges(); renderReader(); renderNotes();
      if (focus) {setMobileTab('notes'); const card = document.getElementById('note-' + state.active); card?.scrollIntoView({block: 'nearest'}); $('.note-comment', card)?.focus();}
      else toast('高亮已保存');
    } finally {busy--; saveStatus();}
  }
  function locateNote(id) {
    state.active = id; setMobileTab('reader'); renderReader();
    const mark = $$('mark').find(m => m.dataset.noteId === id);
    mark?.scrollIntoView({behavior: 'smooth', block: 'center'});
    $$('.note-card').forEach(card => card.classList.toggle('active', card.id === 'note-' + id));
  }
  async function deleteNote(id) {
    if (!await saveNote(id)) return;
    const note = state.notes.find(n => n.id === id);
    const result = await bridge.call('update_annotation', {annotation_id: id, expected_version: note.version, deleted: true});
    state.notes = state.notes.filter(n => n.id !== id); renderReader(); renderNotes();
    toast('已移除高亮和批注', {label: '撤销', run: async () => {
      const restored = await bridge.call('update_annotation', {annotation_id: id, expected_version: result.annotation.version, deleted: false});
      if (state.messages.some(m => m.id === restored.annotation.message_id)) {state.notes.push(restored.annotation); renderReader(); renderNotes();}
      $('#toast').hidden = true;
    }});
  }
  function setMobileTab(tab) {
    $('#workspace').dataset.mobileTab = tab;
    $$('.mobile-tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  }
  function openImport() {$('#import-error').textContent = ''; $('#import-dialog').showModal();}
  function importMode(mode) {
    state.mode = mode;
    for (const item of ['paste', 'thread', 'backup']) $('#' + item + '-fields').hidden = item !== mode;
    $$('[data-import-mode]').forEach(b => b.classList.toggle('active', b.dataset.importMode === mode));
    $('#import-submit').textContent = mode === 'backup' ? '恢复备份' : '加入原文';
  }
  async function doImport(event) {
    event.preventDefault(); if (!await flushAll()) return;
    const button = $('#import-submit'); button.disabled = true; $('#import-error').textContent = '';
    try {
      let data;
      if (state.mode === 'paste') data = await bridge.call('import_text', {title: $('#import-title').value, body: $('#import-body').value});
      else if (state.mode === 'thread') data = await bridge.call('import_thread', {title: $('#thread-title').value, thread_id: $('#thread-id').value.trim()});
      else {
        const file = $('#backup-file').files[0]; if (!file) throw new Error('请先选择备份文件');
        if (file.size > 128000000) throw new Error('备份不能超过 128 MB');
        data = await bridge.call('restore_notes', {archive: await file.text()});
      }
      await library();
      if (data.document) adopt(data); else if (state.documents.length) await loadDocument(state.documents[0].id);
      $('#import-dialog').close(); $('#import-body').value = ''; setMobileTab('reader');
      toast(state.mode === 'backup' ? `已恢复 ${data.restored} 份原文` : '原文已加入，可以开始高亮');
    } catch (error) {$('#import-error').textContent = error.message;}
    finally {button.disabled = false;}
  }
  async function syncThread(background = false) {
    if (syncing || mutating || state.current?.source_kind !== 'local-chat') return;
    if (background && (document.hidden || editing || drafts.size || jobs.size || busy || state.managing || state.selection || $('dialog[open]') || document.activeElement?.matches('textarea,input'))) return;
    syncing = true;
    const button = $('#sync-button'), current = state.current, view = viewPosition(); button.disabled = true;
    try {
      if (!await flushAll()) return;
      const data = await bridge.call('import_thread', {thread_id: current.thread_id, title: current.title});
      if (state.current?.id !== current.id || mutating || drafts.size || state.selection) return;
      const changed = JSON.stringify([data.document.revision, data.messages, data.annotations]) !== JSON.stringify([state.current.revision, state.messages, state.notes]);
      const added = data.messages.length - state.messages.length;
      if (changed || !background) {
        adopt(data, !background || view.bottom ? 'latest' : 'preserve'); await library(); renderEditControls();
        if (background && !view.bottom && added > 0) $('#latest-button').textContent = `${added} 条新消息 ↓`;
      }
      if (!background) toast('已同步到最新消息，批注和删减已保留');
    } catch (error) {if (!background) throw error;}
    finally {syncing = false; button.disabled = false;}
  }
  async function exportNotes(format) {
    if (!await flushAll()) return;
    const buttons = $$('[data-export]'); buttons.forEach(button => button.disabled = true);
    try {
      const args = {format};
      if (['pdf', 'docx'].includes(format)) {
        if (!state.current) throw new Error('请先加入或选择一份原文');
        args.document_id = state.current.id;
        toast('正在生成文档…');
      }
      const data = await bridge.call('export_notes', args); await bridge.download(data);
      $('#export-dialog').close(); toast(data.warnings?.length ? '文档已生成；部分图片未能嵌入，原因已在文档中标明。' : '导出已准备好');
    } finally {buttons.forEach(button => button.disabled = false);}
  }
  async function showRename() {
    if(!state.current||!await flushAll())return;
    $('#rename-title').value=state.current.title;$('#rename-error').textContent='';
    $('#rename-dialog').dataset.documentId=state.current.id;$('#rename-dialog').dataset.revision=state.current.revision;
    $('#rename-dialog').showModal();$('#rename-title').focus();$('#rename-title').select();
  }
  async function renameDocument(event) {
    event.preventDefault();const button=$('#rename-save');button.disabled=true;
    try {
      const dialog=$('#rename-dialog'),title=$('#rename-title').value.trim();if(!title)throw new Error('请输入标题');
      const data=await bridge.call('rename_document',{document_id:dialog.dataset.documentId,title,expected_revision:Number(dialog.dataset.revision)});
      if(state.current?.id===data.document.id)adopt(data);await library();dialog.close();toast('标题已保存');
    }catch(error){$('#rename-error').textContent=error.message;}
    finally{button.disabled=false;}
  }
  async function showCapabilities() {
    $('#connection-info').textContent = bridge.embedded ? `连接方式：MCP App${bridge.hostInfo?.version ? ' · 客户端 ' + bridge.hostInfo.version : ''}。` : '连接方式：本机阅读面板。原生 MCP 面板需安装插件并由客户端加载。';
    $('#capability-dialog').showModal();
  }

  $('#import-button').onclick = openImport;
  $('#appearance-button').onclick = () => {applyAppearance(); $('#appearance-dialog').showModal();};
  $$('[data-color-mode]').forEach(button => button.onclick = () => changeAppearance({mode: button.dataset.colorMode}));
  $('#appearance-reset').onclick = () => changeAppearance({style: 'codex', mode: 'auto'});
  $('#appearance-save-state').onclick = () => {if (appearancePending) rememberAppearance();};
  $('#appearance-done').onclick = safe(async () => {if (await appearanceJob) $('#appearance-dialog').close();});
  $('#export-button').onclick = () => $('#export-dialog').showModal();
  $('#capability-button').onclick = safe(showCapabilities);
  $('#document-select').onchange = safe(event => loadDocument(event.target.value));
  $('#sync-button').onclick = safe(() => syncThread());
  $('#latest-button').onclick = latest;
  $('#manage-button').onclick = () => {state.managing = !state.managing; state.selected.clear(); renderReader(); renderEditControls();};
  $('#select-all-button').onclick = () => {if (state.selected.size === state.messages.length) state.selected.clear(); else state.selected = new Set(state.messages.map(m => m.id)); renderReader(); renderEditControls();};
  $('#delete-selected-button').onclick = safe(() => removeMessages([...state.selected]));
  $('#delete-document-button').onclick = safe(removeDocument);
  $('#undo-button').onclick = safe(() => undoDeletion());
  $('#trash-button').onclick = safe(showTrash);
  $('#source-button').onclick = safe(() => bridge.openChat(state.current.thread_id));
  $('#rename-button').onclick=safe(showRename);
  $('#rename-form').onsubmit=safe(renameDocument);
  $$('[data-close]').forEach(button => button.onclick = () => button.closest('dialog').close());
  $$('[data-import-mode]').forEach(button => button.onclick = () => importMode(button.dataset.importMode));
  $$('[data-export]').forEach(button => button.onclick = safe(() => exportNotes(button.dataset.export)));
  $$('.mobile-tabs button').forEach(button => button.onclick = () => setMobileTab(button.dataset.tab));
  $('#import-form').onsubmit = event => {event.preventDefault(); safe(doImport)(event);};
  $('#note-search').oninput = async event => {state.search = event.target.value; if (await flushAll()) renderNotes();};
  $('#selection-bar').onpointerdown = event => event.preventDefault();
  $('#selection-close').onclick = hideSelection;
  $('#annotate-button').onclick = safe(() => addNote('yellow', true));
  $('#delete-text-button').onclick = safe(removeText);
  for (const color of Object.keys(colors)) $('#selection-colors').append(swatch(color, () => addNote(color), false, '添加' + colors[color] + '高亮'));
  $('#reader').addEventListener('mouseup', captureSelection);
  $('#reader').addEventListener('keyup', captureSelection);
  $('#reader').addEventListener('dblclick',safe(event=>{const body=event.target.closest('.message-body');if(body&&!event.target.closest('a,button,.rich-atomic'))return beginEdit(body.dataset.messageId);}));
  $('#reader').addEventListener('click', event => {
    const mark = event.target.closest('mark');
    clearTimeout(markTimer);
    if (!mark || event.detail > 1) return;
    const id = mark.dataset.noteId;
    // Let a double click select highlighted words before switching the mobile pane.
    markTimer = setTimeout(safe(async () => {
      if (state.selection || !window.getSelection()?.isCollapsed || !state.notes.some(n => n.id === id) || !await flushAll()) return;
      state.active = id; state.filter = ''; state.search = ''; $('#note-search').value = '';
      renderFilters(); renderNotes(); setMobileTab('notes'); document.getElementById('note-' + id)?.scrollIntoView({block: 'nearest'});
    }), 220);
  });
  window.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {event.preventDefault(); safe(flushAll)();}
    if (event.key === 'Escape') hideSelection();
  });
  window.addEventListener('beforeunload', event => {cacheBody();if (drafts.size || jobs.size || editing?.draft!==editing?.original) {event.preventDefault(); event.returnValue = '';}});
  window.addEventListener('pagehide', () => {flushAll();});
  document.addEventListener('visibilitychange', () => {if (document.hidden) flushAll(); else safe(() => syncThread(true))();});
  window.addEventListener('notes-initial-data', event => {
    if (event.detail.document && !editing && !drafts.size && !busy && !mutating) {adopt(event.detail); safe(library)();}
  });
  (async () => {
    renderFilters(); renderReader(); renderNotes(); renderEditControls();
    try {
      await bridge.connect();
      await Promise.all([library(), loadAppearance()]);
      if (bridge.initialData?.document) await loadDocument(bridge.initialData.document.id);
      else if (state.documents.length) await loadDocument(state.documents.find(d => d.id === preferences.get('document'))?.id || state.documents[0].id);
      saveStatus();
      setInterval(() => syncThread(true), 20000);
    } catch (error) {saveStatus(true); toast(error.message);}
  })();
})();
