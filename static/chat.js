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
    content.textContent = text || '';
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
    function read() {
      return reader.read().then(function (result) {
        if (result.done) return;
        buffer += decoder.decode(result.value, { stream: true });
        var events = buffer.split('\n\n');
        buffer = events.pop();
        events.forEach(function (event) {
          event.split('\n').forEach(function (line) {
            if (line.indexOf('data: ') !== 0) return;
            var data = JSON.parse(line.slice(6));
            if (data.type === 'delta') {
              if (reply.classList.contains('reply-loading')) {
                reply.classList.remove('reply-loading');
                reply.textContent = '';
              }
              reply.textContent += data.content || '';
              box.scrollTop = box.scrollHeight;
            } else if (data.type === 'done') {
              sid = data.session_id || sid;
              loadSessions();
            } else if (data.type === 'error') {
              throw new Error(data.error || '流式响应失败');
            }
          });
        });
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
      reply.textContent = '错误：' + error.message;
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
