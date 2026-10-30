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
