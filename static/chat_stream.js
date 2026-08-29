(function () {
  function byId(id) { return document.getElementById(id); }
  var model = document.getElementById('model');
  var box = document.getElementById('messages');
  var list = document.getElementById('sessions');
  var input = document.getElementById('input');
  var status = document.getElementById('status');
  function setStatus(text) { if (status) status['textContent'] = text; }
  function getStatus() { return status ? status['textContent'] : ''; }
  var panel = document.getElementById('conversation-panel');
  var sid = null;
  var busy = false;
  var activeRequest = null;

  function json(url, options) {
    return fetch(url, options).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.error || '请求失败');
        return data;
      });
    });
  }

  function clearMessages() {
    box.innerHTML = '<div id="empty-state" class="empty-state"><div class="empty-logo">Z</div><h1>有什么可以帮忙？</h1></div>';
  }

  function escapeHtml(value) {
    return String(value || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderMarkdown(value) {
    var source = escapeHtml(value);
    var blocks = [];
    source = source.replace(/```([\w.+-]*)\n?([\s\S]*?)```/g, function (_, lang, code) {
      var label = lang ? '<span class=\"code-lang\">' + lang + '</span>' : '';
      blocks.push('<pre class=\"code-block\">' + label + '<code>' + code.replace(/^\n|\n$/g, '') + '</code></pre>');
      return '\n\x00CODE' + (blocks.length - 1) + '\x00\n';
    });
    source = source.replace(/^###### (.+)$/gm, '<h6>$1</h6>')
      .replace(/^##### (.+)$/gm, '<h5>$1</h5>')
      .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^### (.+)$/gm, '<h3>$1</h3>')
      .replace(/^## (.+)$/gm, '<h2>$1</h2>')
      .replace(/^# (.+)$/gm, '<h1>$1</h1>')
      .replace(/^\s*[-*] (.+)$/gm, '<span class=\"md-list\">• $1</span>')
      .replace(/^\s*(\d+)\. (.+)$/gm, '<span class=\"md-list\">$1. $2</span>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code class=\"inline-code\">$1</code>')
      .replace(/\n/g, '<br>');
    source = source.replace(/\x00CODE(\d+)\x00/g, function (_, index) { return blocks[Number(index)]; });
    return source;
  }

  function copyText(text, button) {
    function done() { button.textContent = '已复制'; setTimeout(function () { button.textContent = '复制代码'; }, 1200); }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      var area = document.createElement('textarea'); area.value = text; area.style.position = 'fixed'; area.style.left = '-9999px';
      document.body.appendChild(area); area.focus(); area.select();
      try { if (document.execCommand('copy')) done(); else button.textContent = '复制失败'; } catch (e) { button.textContent = '复制失败'; }
      document.body.removeChild(area);
    }
  }

  function addCopyButtons(content) {
    Array.prototype.forEach.call(content.querySelectorAll('pre.code-block'), function (block) {
      var button = document.createElement('button'); button.type = 'button'; button.className = 'copy-code'; button.textContent = '复制代码';
      button.onclick = function () { copyText(block.querySelector('code').textContent, button); };
      block.appendChild(button);
    });
  }

  function setRendered(content, raw) {
    content.dataset.raw = raw || '';
    content.innerHTML = renderMarkdown(raw || '');
    addCopyButtons(content);
  }

  function addMessage(role, text) {
    var empty = document.getElementById('empty-state');
    if (empty) empty.remove();
    var item = document.createElement('div');
    item.className = 'message ' + (role === '用户' ? 'user-message' : 'assistant-message');
    var who = document.createElement('div');
    who.className = 'message-role';
    who.textContent = role;
    var content = document.createElement('div');
    content.className = 'message-content';
    setRendered(content, text || '');
    item.appendChild(who);
    item.appendChild(content);
    box.appendChild(item);
    box.scrollTop = box.scrollHeight;
    return content;
  }

  function loadingMessage() {
    var content = addMessage('助手', '');
    content.className += ' reply-loading';
    content.innerHTML = '<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span>';
    return content;
  }

  function markSelected() {
    Array.prototype.forEach.call(document.querySelectorAll('.session-item'), function (item) {
      item.classList.toggle('selected', item.dataset.id === sid);
    });
  }

  function openSession(id) {
    sid = id;
    box.innerHTML = '';
    json('/api/sessions/' + encodeURIComponent(id)).then(function (data) {
      (data.messages || []).forEach(function (message) {
        var historyReply = addMessage(message.role === 'user' ? '用户' : '助手', message.content);
        if (message.role !== 'user' && message.meta && message.meta.tool_events) {
          message.meta.tool_events.forEach(function (event) { addToolCard(historyReply, event); });
        }
      });
      markSelected();
    }).catch(function (error) {
      addMessage('助手', '错误：' + error.message);
    });
  }

  function loadSessions() {
    json('/api/sessions').then(function (data) {
      list.innerHTML = '';
      (data.sessions || []).sort(function (a, b) {
        return (b.updated_at || '').localeCompare(a.updated_at || '');
      }).forEach(function (item) {
        var row = document.createElement('div');
        row.className = 'session-row';
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'session-item' + (item.id === sid ? ' selected' : '');
        button.dataset.id = item.id;
        button.textContent = item.title || '新会话';
        button.onclick = function () {
          openSession(item.id);
          panel.classList.remove('mobile-open');
        };
        row.appendChild(button);
        var more = document.createElement('button');
        more.type = 'button'; more.className = 'session-more'; more.title = '会话菜单'; more.textContent = '···';
        var menu = document.createElement('div'); menu.className = 'session-menu'; menu.style.display = 'none';
        var rename = document.createElement('button'); rename.type = 'button'; rename.textContent = '重命名';
        var remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '删除';
        rename.onclick = function (event) { event.stopPropagation(); var title = window.prompt('重命名会话', item.title || '新会话'); if (!title || !title.trim()) return; json('/api/sessions/' + encodeURIComponent(item.id), {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:title.trim()})}).then(loadSessions); };
        remove.onclick = function (event) { event.stopPropagation(); if (!window.confirm('确定删除会话“' + (item.title || '新会话') + '”吗？此操作不可恢复。')) return; json('/api/sessions/' + encodeURIComponent(item.id), {method:'DELETE'}).then(function () { if (sid === item.id) { sid = null; clearMessages(); } loadSessions(); }); };
        menu.appendChild(rename); menu.appendChild(remove); row.appendChild(more); row.appendChild(menu);
        more.onclick = function (event) { event.stopPropagation(); document.querySelectorAll('.session-menu').forEach(function (m) { if (m !== menu) m.style.display = 'none'; }); menu.style.display = menu.style.display === 'none' ? 'block' : 'none'; };
        list.appendChild(row);
      });
    });
  }

  function toolText(value) {
    if (value === undefined || value === null) return '';
    if (typeof value === 'string') return value;
    try { return JSON.stringify(value, null, 2); } catch (e) { return String(value); }
  }

  function addToolCard(reply, data) {
    var item = reply && reply.parentNode;
    if (!item) return;
    var key = String(data.display_name || data.name || data.tool_name || 'tool');
    var card = item.querySelector('.tool-card[data-tool-name="' + key.replace(/"/g, '&quot;') + '"]');
    if (data.type === 'tool_start') {
      card = document.createElement('div');
      card.className = 'tool-card';
      card.setAttribute('data-tool-name', key);
      card.setAttribute('data-tool-state', 'running');
      card.setAttribute('data-started', String(new Date().getTime()));
      var head = document.createElement('button'); head.type = 'button'; head.className = 'tool-head';
      head.innerHTML = '<span class="tool-spinner">◌</span> <span class="tool-state">运行中</span> <strong></strong><span class="tool-time"></span><span class="tool-arrow">⌄</span>';
      var detail = document.createElement('div'); detail.className = 'tool-detail';
      detail.innerHTML = '<div class="tool-label">INPUT</div><pre class="tool-input"></pre><div class="tool-label">OUTPUT</div><pre class="tool-output">等待工具返回...</pre>';
      head.onclick = function () { detail.classList.toggle('show'); card.classList.toggle('tool-collapsed', !detail.classList.contains('show')); };
      card.appendChild(head); card.appendChild(detail); item.appendChild(card);
      card.querySelector('strong').textContent = key;
      card.querySelector('.tool-input').textContent = toolText(data.data || {});
      return;
    }
    var cards = item.querySelectorAll('.tool-card');
    for (var i = cards.length - 1; i >= 0; i--) if (cards[i].getAttribute('data-tool-name') === key && cards[i].getAttribute('data-tool-state') === 'running') { card = cards[i]; break; }
    if (!card) { addToolCard(reply, {type:'tool_start', name:key, data:{}}); return addToolCard(reply, data); }
    card.setAttribute('data-tool-state', 'done');
    var elapsed = new Date().getTime() - Number(card.getAttribute('data-started') || new Date().getTime());
    var state = card.querySelector('.tool-state'); if (state) state.textContent = data.data && data.data.isError ? '失败' : '';
    var spinner = card.querySelector('.tool-spinner'); if (spinner) { spinner.textContent = data.data && data.data.isError ? '✕' : '✓'; spinner.className = 'tool-spinner ' + (data.data && data.data.isError ? 'tool-failed' : 'tool-succeeded'); }
    var time = card.querySelector('.tool-time'); if (time) time.textContent = elapsed + 'ms';
    var output = card.querySelector('.tool-output'); if (output) output.textContent = toolText(data.data);
  }

  function readStream(response, reply) {
    if (!response.body || !response.body.getReader) throw new Error('当前浏览器不支持流式读取，请使用最新版 Chrome 或 Edge');
    var reader = response.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buffer = '';
    function processEvent(event) {
      var lines = event.replace(/\r/g, '').split('\n');
      var payload = [];
      lines.forEach(function (line) { if (line.indexOf('data:') === 0) payload.push(line.slice(5).replace(/^ /, '')); });
      if (!payload.length) return;
      var raw = payload.join('\n');
      if (raw === '[DONE]') return;
      var data = JSON.parse(raw);
      if (data.type === 'tool_start' || data.type === 'tool_result') {
        if (reply.classList.contains('reply-loading')) {
          reply.classList.remove('reply-loading');
          setRendered(reply, '');
        }
        addToolCard(reply, data);
        box.scrollTop = box.scrollHeight;
      } else if (data.type === 'progress' || data.type === 'delta') {
        if (reply.classList.contains('reply-loading')) {
          reply.classList.remove('reply-loading');
          setRendered(reply, '');
        }
        // Keep each Agent text segment in the event timeline. Tool cards are
        // appended to the current segment, so later text starts a new segment.
        var last = reply.parentNode && reply.parentNode.lastElementChild;
        if (last && last.classList && last.classList.contains('tool-card')) {
          var next = document.createElement('div'); next.className = 'message-content agent-progress';
          reply.parentNode.appendChild(next); reply = next;
        }
        setRendered(reply, (reply.dataset.raw || '') + (data.content || ''));
        box.scrollTop = box.scrollHeight;
      } else if (data.type === 'done') {
        sid = data.session_id || sid;
        loadSessions();
      } else if (data.type === 'error') {
        throw new Error(data.error || '流式响应失败');
      }
    }
    function read() {
      return reader.read().then(function (result) {
        buffer += decoder.decode(result.value || new Uint8Array(0), { stream: !result.done });
        var events = buffer.replace(/\r\n/g, '\n').split('\n\n');
        buffer = events.pop();
        events.forEach(processEvent);
        if (result.done) {
          buffer += decoder.decode();
          if (buffer.trim()) processEvent(buffer);
          return;
        }
        return read();
      });
    }
    return read();
  }

  function send() {
    if (busy) return;
    var text = input.value.trim();
    if (!text) return;
    if (!model || !model.value) { setStatus('● 模型加载中，请稍候'); return; }
    busy = true;
    setStatus('● 模型正在回复');
    addMessage('用户', text);
    var reply = loadingMessage();
    input.value = '';
    var attachments = window.chatAttachments || [];
    window.chatAttachments = [];
    var requestOptions = { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' }, body: JSON.stringify({ message: text, model_id: model.value, session_id: sid, attachments: attachments }) };
    if (window.AbortController) { activeRequest = new AbortController(); requestOptions.signal = activeRequest.signal; }
    fetch('/api/chat/stream', requestOptions).then(function (response) {
      if (!response.ok) return response.json().then(function (data) { throw new Error(data.error || '后端请求失败'); });
      return readStream(response, reply);
    }).catch(function (error) {
      reply.classList.remove('reply-loading');
      if (error && error.name === 'AbortError') { setRendered(reply, '请求已取消'); setStatus('● 已取消'); } else { setRendered(reply, '错误：' + error.message); setStatus('● 异常'); }
    }).then(function () {
      if (getStatus() !== '● 异常' && getStatus() !== '● 已取消') setStatus('● 就绪');
      busy = false; activeRequest = null;
    });
  }

  fetch('/api/models').then(function (r) { return r.json(); }).then(function (data) {
    var items = data.models || [];
    model.innerHTML = '';
    if (!items.length) {
      model.innerHTML = '<option value="">未配置模型</option>';
      return;
    }
    items.forEach(function (item) {
      var option = document.createElement('option');
      option.value = item.id;
      option.textContent = item.name || item.id;
      option.selected = !!item.default;
      model.appendChild(option);
    });
    if (!model.value) model.selectedIndex = 0;
  }).catch(function () {
    model.innerHTML = '<option value="">模型加载失败</option>';
  });

  document.getElementById('new').onclick = function (event) {
    event.preventDefault(); sid = null; clearMessages(); markSelected(); input.focus();
  };
  document.addEventListener('click', function () { document.querySelectorAll('.session-menu').forEach(function (m) { m.style.display = 'none'; }); });
  document.getElementById('refresh').onclick = loadSessions;
  document.getElementById('mobile-menu').onclick = function () { panel.classList.toggle('mobile-open'); };
  document.getElementById('logout').onclick = function () {
    fetch('/logout', { method: 'POST', redirect: 'manual' }).then(function () { location = '/login'; });
  };
  function isEnterKey(event) {
    event = event || window.event;
    return event.key === 'Enter' || event.keyCode === 13 || event.which === 13;
  }
  function isComposing(event) {
    event = event || window.event;
    return event.isComposing === true || event.keyCode === 229 || event.which === 229;
  }
  input.onkeydown = function (event) {
    event = event || window.event;
    if (isEnterKey(event) && !event.shiftKey && !event.ctrlKey && !event.altKey && !isComposing(event)) {
      if (event.preventDefault) event.preventDefault();
      event.returnValue = false;
      send();
      return false;
    }
  };
  input.onkeypress = function (event) {
    event = event || window.event;
    if (isEnterKey(event) && !event.shiftKey && !isComposing(event)) {
      if (event.preventDefault) event.preventDefault();
      event.returnValue = false;
      return false;
    }
  };
  var attachButton = byId('attach');
  var fileInput = byId('file-input');
  if (attachButton && fileInput) attachButton.onclick = function (event) {
    if (event && event.preventDefault) event.preventDefault();
    var picker = document.getElementById('file-input');
    if (picker) picker.click();
    return false;
  };
  if (document.getElementById('file-input')) document.getElementById('file-input').onchange = function () {
    var file = this.files && this.files[0];
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { setStatus('● 文件不能超过 10 MB'); this.value = ''; return; }
    window.chatAttachments = window.chatAttachments || [];
    var item = {name: file.name, type: file.type || 'application/octet-stream', size: file.size};
    var reader = new FileReader();
    reader.onload = function () {
      var isImage = /^image\//i.test(item.type);
      var isText = /^(text\/|application\/(json|javascript|xml|yaml|x-yaml)|.*(javascript|json|xml|yaml|csv|plain).*)$/i.test(item.type) || /\.(txt|md|py|js|json|html|css|bat|sh|yaml|yml|xml|csv|ini|log)$/i.test(file.name);
      if (isImage || isText) item.data = reader.result;
      window.chatAttachments.push(item);
      input.value += (input.value ? '\n\n' : '') + '[附件: ' + file.name + ' (' + (item.type || '未知类型') + ')]';
      input.focus(); setStatus('● 已载入 ' + file.name);
    };
    reader.onerror = function () { setStatus('● 文件读取失败'); };
    if (/^image\//i.test(file.type)) reader.readAsDataURL(file); else reader.readAsText(file, 'utf-8');
    this.value = '';
  };
  var cancelButton = byId('cancel');
  if (cancelButton) cancelButton.onclick = function () { if (activeRequest && activeRequest.abort) { activeRequest.abort(); setStatus('● 已取消'); } };
  document.getElementById('send-button').onclick = function (event) { if (event && event.preventDefault) event.preventDefault(); send(); return false; };
  document.getElementById('form').onsubmit = function (event) { event = event || window.event; if (event.preventDefault) event.preventDefault(); event.returnValue = false; send(); return false; };
  // Fallback for old browsers where form submit/key events are inconsistent.
  if (byId('form')) byId('form').onsubmit = function (event) {
    event = event || window.event;
    if (event.preventDefault) event.preventDefault();
    event.returnValue = false;
    send();
    return false;
  };
  loadSessions();
}());
