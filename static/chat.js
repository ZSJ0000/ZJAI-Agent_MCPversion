(function () {
  var model = document.getElementById('model');
  var box = document.getElementById('messages');
  var list = document.getElementById('sessions');
  var input = document.getElementById('input');
  var status = document.getElementById('status');
  var panel = document.getElementById('conversation-panel');
  var sid = null;
  var busy = false;

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
        addMessage(message.role === 'user' ? '用户' : '助手', message.content);
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
        list.appendChild(row);
      });
    });
  }

  function readStream(response, reply) {
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
      if (data.type === 'delta') {
        if (reply.classList.contains('reply-loading')) {
          reply.classList.remove('reply-loading');
          setRendered(reply, '');
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
        var events = buffer.split('\n\n');
        buffer = events.pop();
        events.forEach(processEvent);
        if (result.done) {
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
    if (!text || !model.value) return;
    busy = true;
    status.textContent = '● 模型正在回复';
    addMessage('用户', text);
    var reply = loadingMessage();
    input.value = '';
    fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, model_id: model.value, session_id: sid })
    }).then(function (response) {
      if (!response.ok) return response.json().then(function (data) { throw new Error(data.error || '后端请求失败'); });
      return readStream(response, reply);
    }).catch(function (error) {
      reply.classList.remove('reply-loading');
      setRendered(reply, '错误：' + error.message);
      status.textContent = '● 异常';
    }).then(function () {
      if (status.textContent !== '● 异常') status.textContent = '● 就绪';
      busy = false;
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
  document.getElementById('refresh').onclick = loadSessions;
  document.getElementById('mobile-menu').onclick = function () { panel.classList.toggle('mobile-open'); };
  document.getElementById('logout').onclick = function () {
    fetch('/logout', { method: 'POST', redirect: 'manual' }).then(function () { location = '/login'; });
  };
  input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); }
  });
  document.getElementById('form').onsubmit = function (event) { event.preventDefault(); send(); };
  loadSessions();
}());
