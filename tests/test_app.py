import os, sys, json, tempfile, copy, importlib

import pytest


def _reset_modules():
    """清掉 app 包及其子模块，避免上一个测试的 DATA_DIR + 缓存状态泄漏。"""
    for name in list(sys.modules):
        if name == 'app' or name.startswith('app.'):
            del sys.modules[name]


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
    """把 app 的数据/配置目录指到临时目录，避免测试之间以及和仓库 data/ 互相污染。"""
    os.environ['ZJAI_SECRET_KEY'] = 'test'
    _reset_modules()

    data_dir = str(tmp_path / 'data')
    config_dir = str(tmp_path / 'config')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(config_dir, exist_ok=True)

    # 必须在 app 包被导入前就把目录改掉：app/__init__.py 与 services/json_store.py
    # 都执行 from .config import DATA_DIR，导入后这些名字固化成值，事后 patch 不生效。
    # 注意要 patch sys.modules 里的真正模块对象：包初始化过程中 import app.config
    # 可能拿到另一个副本，改副本上的属性会被 from .config import 忽略。
    import app as app_pkg  # noqa: F401  确保包已完成初始化
    import app.services.json_store  # noqa: F401  确保它已完成 from .config import
    import app.services.auth_service  # noqa: F401  同上

    cfg = sys.modules['app.config']
    cfg.DATA_DIR = data_dir
    cfg.CONFIG_DIR = config_dir

    store = sys.modules['app.services.json_store']
    store.DATA_DIR = data_dir
    store.CONFIG_DIR = config_dir

    # 这几个模块都用 from ... import DATA_DIR 绑定了值，必须逐个覆盖。
    for mod_name in list(sys.modules):
        if not (mod_name == 'app' or mod_name.startswith('app.')):
            continue
        module = sys.modules[mod_name]
        if getattr(module, 'DATA_DIR', None) is not None:
            module.DATA_DIR = data_dir
        if getattr(module, 'CONFIG_DIR', None) is not None:
            module.CONFIG_DIR = config_dir

    os.chdir(str(tmp_path))
    return tmp_path


def _fresh_app(tmpdir=None):
    os.chdir(tmpdir or tempfile.mkdtemp())
    os.environ['ZJAI_SECRET_KEY'] = 'test'
    import app as app_module
    return app_module.create_app()


def test_setup_login_and_sessions(isolated_data):
    app = _fresh_app(str(isolated_data))
    c = app.test_client()
    assert c.get('/setup').status_code == 200
    # 安装向导共 4 步：1 欢迎，2 建账号，3 配 API，4 站点信息后落盘
    assert c.post('/setup/2', data={'username': 'admin', 'password': 'password', 'confirm': 'password'}).status_code == 302
    assert c.post('/setup/3', data={
        'api_name[]': ['local'],
        'api_base_url[]': ['http://127.0.0.1:11434/v1'],
        'api_key[]': ['sk-test'],
        'api_model[]': ['llama3'],
    }).status_code == 302
    assert c.post('/setup/4', data={'site_name': 'ZJAI'}).status_code == 302
    assert c.post('/login', data={'username': 'admin', 'password': 'password'}).status_code == 302
    assert c.get('/api/sessions').status_code == 200


@pytest.fixture()
def mcp(monkeypatch):
    os.chdir(tempfile.mkdtemp())
    os.environ['ZJAI_SECRET_KEY'] = 'test'
    _reset_modules()
    import app.services.mcp_service as svc
    return svc


def _server(server_id, url=None):
    return {
        'id': server_id,
        'name': server_id,
        'url': url or 'http://%s/mcp' % server_id,
        'enabled': True,
        'protocol': 'streamable_http',
        'headers': {},
        'verify_ssl': True,
    }


def test_mcp_namespaced_tools_route_and_session_cache(mcp, monkeypatch):
    hosts = [_server('main'), _server('filesystem')]
    monkeypatch.setattr(mcp, 'servers', lambda: copy.deepcopy(hosts))

    initialize_calls = []
    tool_calls = []
    session_for = {}

    tool_def = {
        'name': 'create_file',
        'description': 'create a file',
        'inputSchema': {
            'type': 'object',
            'properties': {'path': {'type': 'string'}},
            'required': ['path'],
        },
    }

    def fake_post(url, payload, h, s, expect_json=True):
        method = payload.get('method')
        sid = s.get('id')

        if method == 'initialize':
            initialize_calls.append(sid)
            session_for[sid] = 'sess-' + sid
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'protocolVersion': '2024-11-05'}}, session_for[sid]

        if method == 'notifications/initialized':
            assert expect_json is False
            return {}, None

        if method == 'tools/list':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'tools': [copy.deepcopy(tool_def)]}}, None

        if method == 'tools/call':
            tool_calls.append({'server': sid, 'name': payload['params']['name'], 'session': h.get('Mcp-Session-Id')})
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'content': [{'type': 'text', 'text': 'ok'}]}}, None

        raise AssertionError('unexpected method: %s' % method)

    monkeypatch.setattr(mcp, '_post', fake_post)

    tools, errors = mcp.list_tools_details()
    assert errors == []
    assert {t['name'] for t in tools} == {'main__create_file', 'filesystem__create_file'}

    # session 复用：再次列工具不应重新 initialize
    mcp.list_tools_details()
    assert initialize_calls == ['main', 'filesystem']

    # 同名工具按前缀路由到各自服务
    mcp.call_tool('main__create_file', {'path': 'a.txt'})
    mcp.call_tool('filesystem__create_file', {'path': 'b.txt'})
    assert [c['server'] for c in tool_calls] == ['main', 'filesystem']
    assert [c['name'] for c in tool_calls] == ['create_file', 'create_file']
    # 每次调用都带上各自缓存的 session id
    assert [c['session'] for c in tool_calls] == ['sess-main', 'sess-filesystem']

    # 缺参时本地 schema 校验拦截，且带出字段路径
    bad = mcp.call_tool('main__create_file', {})
    assert bad['isError'] is True
    assert bad['details'][0]['path'] == 'path'

    # 缺服务前缀时报错
    no_prefix = mcp.call_tool('create_file', {'path': 'x'})
    assert no_prefix['isError'] is True


def test_mcp_tools_failure_is_not_cached(mcp, monkeypatch):
    hosts = [_server('main')]
    monkeypatch.setattr(mcp, 'servers', lambda: copy.deepcopy(hosts))

    def fake_post(url, payload, h, s, expect_json=True):
        method = payload.get('method')
        if method == 'initialize':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'protocolVersion': '2024-11-05'}}, 'sess'
        if method == 'notifications/initialized':
            return {}, None
        if method == 'tools/list':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'error': {'code': -32000, 'message': 'tools/list failed'}}, None
        raise AssertionError(method)

    monkeypatch.setattr(mcp, '_post', fake_post)

    tools, errors = mcp.list_tools_details()
    assert tools == []
    assert any('tools/list failed' in e for e in errors)
    assert mcp._cache_key(hosts[0]) not in mcp._TOOLS_CACHE


def test_text_tool_name_with_double_underscore():
    os.chdir(tempfile.mkdtemp())
    os.environ['ZJAI_SECRET_KEY'] = 'test'
    _reset_modules()
    import app.services.model_service as model_service

    token_style = {
        'role': 'assistant',
        'content': '<tool_call>{"name":"main__create_file","arguments":{}}</tool_call>',
    }
    calls = model_service._extract_tool_calls(token_style)
    assert calls and calls[0]['function']['name'] == 'main__create_file'

    fn_style = {
        'role': 'assistant',
        'content': 'main__create_file({"x":"test"})',
    }
    calls = model_service._extract_tool_calls(fn_style)
    assert calls and calls[0]['function']['name'] == 'main__create_file'


def test_parse_tool_name_handles_double_underscore_in_server_id(mcp, monkeypatch):
    hosts = [_server('my__server'), _server('my')]
    monkeypatch.setattr(mcp, 'servers', lambda: copy.deepcopy(hosts))

    assert mcp._parse_tool_name('my__server__create_file') == ('my__server', 'create_file')
    assert mcp._parse_tool_name('my__create_file') == ('my', 'create_file')
    assert mcp._parse_tool_name('noseparator') == (None, None)


def test_tools_cache_hits_and_returns_deep_copies(mcp, monkeypatch):
    hosts = [_server('main')]
    monkeypatch.setattr(mcp, 'servers', lambda: copy.deepcopy(hosts))

    list_calls = []

    def fake_post(url, payload, h, s, expect_json=True):
        method = payload.get('method')
        if method == 'initialize':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {}}, 'sess'
        if method == 'notifications/initialized':
            return {}, None
        if method == 'tools/list':
            list_calls.append(1)
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'tools': [
                {'name': 'create_file', 'inputSchema': {}},
            ]}}, None
        raise AssertionError(method)

    monkeypatch.setattr(mcp, '_post', fake_post)

    first = mcp.list_tools()
    assert len(list_calls) == 1

    # 缓存命中：不应再次请求 tools/list
    second = mcp.list_tools()
    assert len(list_calls) == 1

    # deepcopy 隔离：改动返回值不得污染缓存
    second[0]['name'] = 'CORRUPTED'
    third = mcp.list_tools()
    assert third[0]['name'] == 'main__create_file'

    # force_refresh 必须绕过缓存
    mcp._get_server_tools(hosts[0], force_refresh=True)
    assert len(list_calls) == 2


def test_missing_tools_field_does_not_write_cache(mcp, monkeypatch):
    """result 里没有 tools 字段时必须报错，且不得写空列表进缓存（否则会永久缓存空结果）。"""
    hosts = [_server('main')]
    monkeypatch.setattr(mcp, 'servers', lambda: copy.deepcopy(hosts))

    def fake_post(url, payload, h, s, expect_json=True):
        method = payload.get('method')
        if method == 'initialize':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {}}, 'sess'
        if method == 'notifications/initialized':
            return {}, None
        if method == 'tools/list':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {}}, None
        raise AssertionError(method)

    monkeypatch.setattr(mcp, '_post', fake_post)

    tools, errors = mcp.list_tools_details()
    assert tools == []
    assert errors
    assert mcp._cache_key(hosts[0]) not in mcp._TOOLS_CACHE


def test_cache_read_is_deepcopied(mcp, monkeypatch):
    """直接篡改缓存里的对象，不应影响后续读到或返回的结果。"""
    host = _server('main')
    monkeypatch.setattr(mcp, 'servers', lambda: [copy.deepcopy(host)])

    def fake_post(url, payload, h, s, expect_json=True):
        method = payload.get('method')
        if method == 'initialize':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {}}, 'sess'
        if method == 'notifications/initialized':
            return {}, None
        if method == 'tools/list':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'tools': [
                {'name': 'create_file', 'inputSchema': {}},
            ]}}, None
        raise AssertionError(method)

    monkeypatch.setattr(mcp, '_post', fake_post)

    mcp._get_server_tools(host)
    key = mcp._cache_key(host)
    assert key in mcp._TOOLS_CACHE

    # 篡改缓存内容，下一次读取不得把被污染的对象交出去
    mcp._TOOLS_CACHE[key]['tools'][0]['name'] = 'CORRUPTED'
    again = mcp._get_server_tools(host)
    assert again[0]['name'] == 'CORRUPTED'  # 缓存读本身不做清洗，确认我们读的是缓存

    # 但返回值必须是副本：改动返回值不影响缓存内部
    again[0]['name'] = 'MUTATED'
    assert mcp._TOOLS_CACHE[key]['tools'][0]['name'] == 'CORRUPTED'


def test_returned_tools_do_not_alias_cache(mcp, monkeypatch):
    """两次调用返回的对象必须互相独立，不能是同一份缓存引用。"""
    host = _server('main')
    monkeypatch.setattr(mcp, 'servers', lambda: [copy.deepcopy(host)])

    def fake_post(url, payload, h, s, expect_json=True):
        method = payload.get('method')
        if method == 'initialize':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {}}, 'sess'
        if method == 'notifications/initialized':
            return {}, None
        if method == 'tools/list':
            return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'tools': [
                {'name': 'create_file', 'inputSchema': {}},
            ]}}, None
        raise AssertionError(method)

    monkeypatch.setattr(mcp, '_post', fake_post)

    first = mcp._get_server_tools(host)
    first[0]['name'] = 'MUTATED'
    second = mcp._get_server_tools(host)
    assert second[0]['name'] == 'create_file'
    assert first is not second


def _setup_admin(iso):
    """跑完 4 步安装向导并以管理员登录，返回已登录的 test_client。"""
    app = _fresh_app(str(iso))
    c = app.test_client()
    c.get('/setup')
    c.post('/setup/2', data={'username': 'admin', 'password': 'password', 'confirm': 'password'})
    c.post('/setup/3', data={
        'api_name[]': ['local'],
        'api_base_url[]': ['http://127.0.0.1:11434/v1'],
        'api_key[]': ['sk-test'],
        'api_model[]': ['llama3'],
    })
    c.post('/setup/4', data={'site_name': 'ZJAI'})
    assert c.post('/login', data={'username': 'admin', 'password': 'password'}).status_code == 302
    return c


def test_admin_page_renders_real_console(isolated_data):
    """管理后台必须是可用的控制台，不是占位文案。"""
    c = _setup_admin(isolated_data)
    r = c.get('/AdminX')
    assert r.status_code == 200
    html = r.data.decode('utf-8')
    assert '用户管理' in html
    assert '运行配置概览' in html
    # 占位时期的文案不应再出现
    assert '接口已预留' not in html


def test_admin_user_lifecycle(isolated_data):
    """创建用户 -> 出现在列表 -> 停用 -> 启用。"""
    c = _setup_admin(isolated_data)

    assert c.post('/AdminX/api/users', json={'username': 'bob', 'password': 'secret1'}).status_code == 200
    users = c.get('/AdminX/api/users').get_json()['users']
    bob = next(u for u in users if u['username'] == 'bob')
    assert bob['role'] == 'user'
    assert bob['enabled'] is True

    assert c.post('/AdminX/api/users/%s/toggle' % bob['id']).status_code == 200
    users = c.get('/AdminX/api/users').get_json()['users']
    bob = next(u for u in users if u['username'] == 'bob')
    assert bob['enabled'] is False

    assert c.post('/AdminX/api/users/%s/toggle' % bob['id']).status_code == 200
    users = c.get('/AdminX/api/users').get_json()['users']
    assert next(u for u in users if u['username'] == 'bob')['enabled'] is True


def test_admin_password_is_masked(isolated_data):
    """概览接口必须脱敏，不能把 api_key / headers 明文交出去。"""
    c = _setup_admin(isolated_data)
    cfg = c.get('/AdminX/api/config').get_json()
    assert cfg['models']
    for m in cfg['models']:
        assert m['api_key'] in ('********', '', None)
    for s in cfg['mcp']:
        assert s['headers'] in ('********', {}, None)


def test_admin_endpoints_reject_non_admin(isolated_data):
    """普通用户访问管理接口必须 403。"""
    c = _setup_admin(isolated_data)
    assert c.post('/AdminX/api/users', json={'username': 'eve', 'password': 'secret1'}).status_code == 200
    c.post('/logout')
    assert c.post('/login', data={'username': 'eve', 'password': 'secret1'}).status_code == 302

    assert c.get('/AdminX').status_code == 403
    assert c.get('/AdminX/api/users').status_code == 403
    assert c.post('/AdminX/api/users', json={'username': 'x', 'password': 'secret1'}).status_code == 403
    assert c.get('/AdminX/api/config').status_code == 403
    assert c.get('/AdminX/api/settings').status_code == 403


def test_json_store_write_holds_lock(isolated_data):
    """write 的 mkstemp + os.replace 必须在锁内，否则并发保存会丢更新。

    直接测不变量：持锁期间调用 write 必须阻塞，而不是抢先完成。
    """
    import threading
    import app.services.json_store as store

    held = threading.Event()
    release = threading.Event()

    def holder():
        with store.L:
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(5)

    finished = []

    def writer():
        store.save_data('lock.json', {'ok': True})
        finished.append(True)

    w = threading.Thread(target=writer)
    w.start()
    w.join(0.3)
    # 锁被占用时写入不得完成
    assert finished == [], 'write() 没有等待锁，并发保存会互相覆盖'
    release.set()
    w.join(5)
    t.join(5)
    assert finished == [True]
    assert store.data('lock.json', None) == {'ok': True}


def test_json_store_write_leaves_no_temp_file_on_error(isolated_data):
    """序列化失败时不能残留 mkstemp 临时文件。"""
    import app.services.json_store as store

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        store.save_data('bad.json', {'x': Unserializable()})

    leftovers = [f for f in os.listdir(str(isolated_data / 'data')) if f.startswith('tmp')]
    assert leftovers == []


@pytest.mark.parametrize('url,needles', [
    ('/chat', ['chat-layout', 'composer', 'messages', 'chat_stream.js']),
    ('/settings', ['settings-menu']),
    ('/settings/models', ['模型 API', 'id="models"']),
    ('/settings/mcp', ['MCP 服务', 'test-mcp']),
    ('/settings/appearance', ['个性化设置']),
    ('/settings/password', ['修改密码']),
    ('/settings/skills', ['Skill']),
    ('/AdminX', ['用户管理', '运行配置概览', 'cfg-mcp']),
])
def test_pages_render_required_elements(isolated_data, url, needles):
    """每个页面都要渲染出核心元素，防止模板改动把页面改空。"""
    c = _setup_admin(isolated_data)
    r = c.get(url)
    assert r.status_code == 200, url
    html = r.data.decode('utf-8')
    missing = [n for n in needles if n not in html]
    assert not missing, '%s 缺少元素: %s' % (url, missing)


def test_stylesheet_is_well_formed(isolated_data):
    """样式表括号必须配平，且覆盖层变量已生效。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'terminal.css')
    css = open(path, encoding='utf-8').read()
    assert css.count('{') == css.count('}')
    assert '--zj-accent' in css
    assert ':focus-visible' in css
    # 基础可访问性：必须声明视口与减少动效偏好
    assert 'prefers-reduced-motion' in css


def _set_skin(c, skin):
    r = c.put('/AdminX/api/app-settings', json={'name': 'ZJAI', 'skin': skin})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['app']


CHAT_CONTRACT_IDS = [
    'conversation-panel', 'empty-state', 'file-input', 'form', 'input', 'logout',
    'messages', 'mobile-menu', 'model', 'new', 'refresh', 'send-button', 'sessions', 'status',
]


def test_chat_dom_contract_is_intact(isolated_data):
    """chat_stream.js 依赖这些 ID，缺失会导致聊天功能静默失效。"""
    c = _setup_admin(isolated_data)
    html = c.get('/chat').data.decode('utf-8')
    missing = [i for i in CHAT_CONTRACT_IDS if 'id="%s"' % i not in html]
    assert not missing, '缺少 chat_stream.js 依赖的 ID: %s' % missing


def test_skin_switch_loads_aurora_css(isolated_data):
    """切到 aurora 后应引入新样式表并打上 skin 类，切回 terminal 则都不出现。"""
    c = _setup_admin(isolated_data)

    _set_skin(c, 'aurora')
    html = c.get('/chat').data.decode('utf-8')
    assert 'aurora.css' in html
    assert 'skin-aurora' in html
    # 基线样式表仍加载，aurora 只做覆盖
    assert 'terminal.css' in html
    assert all('id="%s"' % i in html for i in CHAT_CONTRACT_IDS)

    _set_skin(c, 'terminal')
    html = c.get('/chat').data.decode('utf-8')
    assert 'aurora.css' not in html
    assert 'skin-terminal' in html


def test_skin_applies_to_all_pages(isolated_data):
    """皮肤应作用于所有页面，而不是只有聊天页。"""
    c = _setup_admin(isolated_data)
    _set_skin(c, 'aurora')
    for url in ('/chat', '/settings', '/settings/models', '/AdminX'):
        html = c.get(url).data.decode('utf-8')
        assert 'skin-aurora' in html, url


def test_unknown_skin_is_rejected(isolated_data):
    c = _setup_admin(isolated_data)
    r = c.put('/AdminX/api/app-settings', json={'skin': 'nope'})
    assert r.status_code == 400
    assert c.get('/chat').data.decode('utf-8').count('skin-aurora') == 0


def test_theme_is_alias_for_skin(isolated_data):
    """老客户端传 theme 也要能生效，且保存后两者一致。"""
    c = _setup_admin(isolated_data)
    r = c.put('/AdminX/api/app-settings', json={'theme': 'aurora'})
    assert r.status_code == 200
    app_cfg = r.get_json()['app']
    assert app_cfg['skin'] == 'aurora'
    assert app_cfg['theme'] == 'aurora'


def test_saving_settings_does_not_reset_skin(isolated_data):
    """只改欢迎语时不得把皮肤悄悄重置回默认值。"""
    c = _setup_admin(isolated_data)
    _set_skin(c, 'aurora')
    r = c.put('/AdminX/api/app-settings', json={'welcome_message': '你好'})
    assert r.status_code == 200
    assert r.get_json()['app']['skin'] == 'aurora'


def test_message_page_renders_content(isolated_data):
    """message.html 曾缺少 content block，导致页面空白。"""
    import app.services.json_store as store
    from app.config import DATA_DIR
    store.write(os.path.join(DATA_DIR, 'install.lock'), {'initialized': True})
    c = _fresh_app(str(isolated_data)).test_client()
    html = c.get('/setup').data.decode('utf-8')
    assert '系统已初始化' in html or '已初始化' in html


def test_mcp_endpoints_require_login(isolated_data):
    """MCP 工具清单与诊断会暴露服务拓扑，未登录必须拒绝。"""
    c = _setup_admin(isolated_data)
    c.post('/logout')
    assert c.get('/api/mcp/tools').status_code == 401
    assert c.get('/api/mcp/diagnostics').status_code == 401


def test_oversized_attachment_is_rejected(isolated_data):
    """后端必须独立校验附件大小，不能只靠前端。"""
    c = _setup_admin(isolated_data)
    import app as app_module
    huge = 'data:image/png;base64,' + 'A' * (app_module.MAX_ATTACHMENT_CHARS + 10)
    r = c.post('/api/chat/stream', json={'message': 'hi', 'attachments': [{'data': huge}]})
    assert r.status_code == 413


def test_too_many_attachments_rejected(isolated_data):
    c = _setup_admin(isolated_data)
    import app as app_module
    many = [{'name': 'a.txt', 'type': 'text/plain', 'size': 1, 'data': ''}] * (app_module.MAX_ATTACHMENTS + 1)
    r = c.post('/api/chat/stream', json={'message': 'hi', 'attachments': many})
    assert r.status_code == 400


def test_request_body_limit_is_configured(isolated_data):
    """必须有请求体上限，否则客户端可绕过前端直接推超大 payload。"""
    app = _fresh_app(str(isolated_data))
    assert app.config.get('MAX_CONTENT_LENGTH')
    assert app.config['MAX_CONTENT_LENGTH'] <= 64 * 1024 * 1024


def test_saving_models_keeps_existing_api_key(isolated_data):
    """保存模型配置时，前端回传的掩码不得覆盖真实 key。"""
    c = _setup_admin(isolated_data)
    r = c.put('/AdminX/api/models', json={'models': [
        {'id': 'api-1', 'name': 'A', 'base_url': 'http://x/v1', 'model': 'm1', 'api_key': 'sk-real'},
    ]})
    assert r.status_code == 200

    # 前端把掩码原样传回来，应保留原 key
    r = c.put('/AdminX/api/models', json={'models': [
        {'id': 'api-1', 'name': 'A', 'base_url': 'http://x/v1', 'model': 'm1', 'api_key': '********'},
    ]})
    assert r.status_code == 200

    import app.services.json_store as store
    saved = store.config('models.json', {}).get('models', [])
    assert saved[0]['api_key'] == 'sk-real'


def test_saving_mcp_keeps_existing_token(isolated_data):
    c = _setup_admin(isolated_data)
    r = c.put('/AdminX/api/mcp', json={'servers': [
        {'id': 's1', 'name': 'S', 'protocol': 'streamable_http', 'url': 'http://x/mcp', 'headers': {'Authorization': 'Bearer tok'}},
    ]})
    assert r.status_code == 200

    r = c.put('/AdminX/api/mcp', json={'servers': [
        {'id': 's1', 'name': 'S', 'protocol': 'streamable_http', 'url': 'http://x/mcp', 'headers': {}},
    ]})
    assert r.status_code == 200

    import app.services.json_store as store
    saved = store.config('mcp.json', {})['servers']
    # 留空表示"保留原值"，原样存回（含 Bearer 前缀）
    assert saved[0]['headers'].get('Authorization') == 'Bearer tok'


def test_models_default_is_always_set(isolated_data):
    """没有任何模型标记 default 时，第一个要自动成为默认。"""
    c = _setup_admin(isolated_data)
    c.put('/AdminX/api/models', json={'models': [
        {'name': 'A', 'base_url': 'http://x/v1', 'model': 'm1'},
        {'name': 'B', 'base_url': 'http://y/v1', 'model': 'm2'},
    ]})
    import app.services.json_store as store
    saved = store.config('models.json', {})['models']
    assert sum(1 for m in saved if m['default']) == 1
    assert saved[0]['default'] is True


def test_streaming_request_is_not_retried(isolated_data):
    """流式请求不能重试：已开始消费的响应无法重放，重试会重复计费。"""
    import app.services.model_service as ms

    calls = []

    class FakeResp:
        status_code = 503
        def close(self): pass

    def fake_post(url, **kwargs):
        calls.append(kwargs.get('stream'))
        return FakeResp()

    original = ms.requests.post
    ms.requests.post = fake_post
    try:
        resp = ms._post_with_retry('http://x', stream=True)
        assert resp.status_code == 503
        assert calls == [True], '流式请求应只发一次，实际: %r' % calls

        calls.clear()
        resp = ms._post_with_retry('http://x')
        assert len(calls) == ms.API_RETRIES, '非流式请求应重试 %d 次，实际 %d' % (ms.API_RETRIES, len(calls))
    finally:
        ms.requests.post = original


def test_retry_closes_failed_response(isolated_data):
    """重试前必须关闭上一个响应，否则连接被占用。"""
    import app.services.model_service as ms

    closed = []

    class FakeResp:
        status_code = 500
        def close(self): closed.append(True)

    original_post, original_sleep = ms.requests.post, ms.time.sleep
    ms.requests.post = lambda url, **kw: FakeResp()
    ms.time.sleep = lambda s: None
    try:
        ms._post_with_retry('http://x')
        assert len(closed) == ms.API_RETRIES - 1, closed
    finally:
        ms.requests.post, ms.time.sleep = original_post, original_sleep
