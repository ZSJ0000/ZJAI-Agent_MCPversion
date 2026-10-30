import copy
import json
import requests
import threading
import time
import uuid
from urllib.parse import urljoin

from .json_store import config


class MCPError(Exception):
    pass


class MCPHttpError(MCPError):
    def __init__(self, message, status_code=None):
        MCPError.__init__(self, message)
        self.status_code = status_code


try:
    from jsonschema import Draft7Validator
    from jsonschema import validators as _js_validators

    _HAS_JSONSCHEMA = True
except ImportError:
    Draft7Validator = None
    _js_validators = None
    _HAS_JSONSCHEMA = False


_NS_SEP = '__'
_SESSION_TTL = 300
_TOOLS_TTL = 300

# 缓存条目上限。改配置会换 key，若不设上限，反复改配置会让缓存无限增长。
_MAX_CACHE_ENTRIES = 64

_SESSION_CACHE = {}
_TOOLS_CACHE = {}
_CACHE_LOCK = threading.RLock()


def _prune_cache(cache, now):
    """清掉过期条目；若仍超上限，按过期时间淘汰最旧的。调用方需持锁。"""
    expired = [k for k, v in cache.items() if (v or {}).get('expires_at', 0) <= now]
    for k in expired:
        cache.pop(k, None)
    if len(cache) > _MAX_CACHE_ENTRIES:
        keep = sorted(cache.items(), key=lambda kv: (kv[1] or {}).get('expires_at', 0))
        for k, _ in keep[:len(cache) - _MAX_CACHE_ENTRIES]:
            cache.pop(k, None)


def _proto(server):
    return server.get('protocol', 'streamable_http')


def _cache_key(server):
    return (
        server.get('id'),
        server.get('url'),
        _proto(server),
    )


def _invalidate_cache(server):
    key = _cache_key(server)

    with _CACHE_LOCK:
        _SESSION_CACHE.pop(key, None)
        _TOOLS_CACHE.pop(key, None)


def invalidate_all_cache():
    with _CACHE_LOCK:
        _SESSION_CACHE.clear()
        _TOOLS_CACHE.clear()


def servers(): return config('mcp.json', {'servers':[]}).get('servers',[])
def _headers(s):
    h=dict(s.get('headers') or {})
    if not s.get('oauth_enabled', bool(h.get('Authorization'))): h.pop('Authorization',None)
    h.setdefault('Accept','application/json, text/event-stream')
    return h

def _json_response(r):
    text=r.text.strip()
    if text.startswith('data:'): text=text[5:].strip()
    try: return r.json() if text==r.text.strip() else json.loads(text)
    except ValueError: raise MCPError('MCP 返回不是 JSON: '+text[:300])

def _post(url,payload,h,s,expect_json=True):
    try:
        r=requests.post(url,json=payload,headers=h,timeout=60,verify=s.get('verify_ssl',True))
    except requests.RequestException as exc:
        raise MCPError('请求 %s 失败: %s' % (url,exc))
    if not r.ok:
        raise MCPHttpError(
            '请求 %s 返回 HTTP %s: %s' % (url, r.status_code, r.text[:300]),
            r.status_code,
        )
    sid=r.headers.get('Mcp-Session-Id') or r.headers.get('mcp-session-id')
    if not expect_json or not r.text.strip(): return {},sid
    return _json_response(r),sid

def _sse_endpoint(s,h):
    r=requests.get(s['url'],headers=h,stream=True,timeout=30,verify=s.get('verify_ssl',True)); r.raise_for_status()
    for raw in r.iter_lines(decode_unicode=True):
        line=(raw or '').strip()
        if line.startswith('data:') and line[5:].strip():
            endpoint=urljoin(s['url'],line[5:].strip()); r.close(); return endpoint
    r.close(); raise MCPError('SSE 未返回 endpoint')

def _raw_streamable_request(server, method, params=None, session_id=None, expect_json=True):
    headers = _headers(server)
    if session_id:
        headers['Mcp-Session-Id'] = session_id
    payload = {
        'jsonrpc': '2.0',
        'id': str(uuid.uuid4()),
        'method': method,
        'params': params or {},
    }
    return _post(server['url'], payload, headers, server, expect_json=expect_json)

def _raw_sse_request(server, method, params=None, session_id=None, expect_json=True):
    headers = _headers(server)
    endpoint = _sse_endpoint(server, headers)
    if session_id:
        headers['Mcp-Session-Id'] = session_id
    payload = {
        'jsonrpc': '2.0',
        'id': str(uuid.uuid4()),
        'method': method,
        'params': params or {},
    }
    return _post(endpoint, payload, headers, server, expect_json=expect_json)

def _initialize_streamable_session(server):
    init, session_id = _raw_streamable_request(
        server, 'initialize',
        {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'ZJAI-Agent', 'version': '1.0'},
        },
    )
    if not isinstance(init, dict) or init.get('error'):
        return None, False
    _raw_streamable_request(
        server, 'notifications/initialized', {},
        session_id=session_id, expect_json=False,
    )
    return session_id, True

def _get_session(server):
    if _proto(server) != 'streamable_http':
        return None, False, True

    key = _cache_key(server)
    now = time.time()

    with _CACHE_LOCK:
        cached = _SESSION_CACHE.get(key)
        if cached and cached.get('expires_at', 0) > now:
            return cached.get('session_id'), True, True

    sid, ok = _initialize_streamable_session(server)
    if not ok:
        return None, False, False

    with _CACHE_LOCK:
        _SESSION_CACHE[key] = {
            'session_id': sid,
            'stateless': sid is None,
            'expires_at': now + _SESSION_TTL,
        }
        _prune_cache(_SESSION_CACHE, now)
    return sid, False, True

def _looks_like_session_expired(text):
    value = str(text).lower()
    return 'session' in value and any(x in value for x in ('not found', 'expired', 'invalid'))

def _request(server, method, params=None):
    protocol = _proto(server)

    if protocol == 'sse':
        response, _ = _raw_sse_request(server, method, params)
        return response

    if protocol != 'streamable_http':
        raise MCPError('不支持的 MCP 协议: %s' % protocol)

    sid, cached, ok = _get_session(server)
    if not ok:
        return {
            'error': {
                'code': 'mcp_initialize_failed',
                'message': 'MCP 服务 %s 初始化失败' % server.get('id'),
            }
        }

    try:
        response, _ = _raw_streamable_request(server, method, params, session_id=sid)
    except MCPHttpError as exc:
        _invalidate_cache(server)
        should_retry = (
            exc.status_code == 401 or
            (exc.status_code == 404 and _looks_like_session_expired(str(exc)))
        )
        if not should_retry:
            raise
        sid, cached, ok = _get_session(server)
        if not ok:
            raise
        response, _ = _raw_streamable_request(server, method, params, session_id=sid)

    return response

def _get_server_tools(server, force_refresh=False):
    protocol = _proto(server)
    key = _cache_key(server)
    now = time.time()

    if protocol == 'streamable_http' and not force_refresh:
        with _CACHE_LOCK:
            cached = _TOOLS_CACHE.get(key)
            if cached and cached.get('expires_at', 0) > now:
                return copy.deepcopy(cached['tools'])

    response = _request(server, 'tools/list', {})

    if isinstance(response, dict) and response.get('error'):
        raise MCPError(
            'MCP 服务 %s 获取工具失败: %s' % (server.get('id'), response['error'])
        )

    tools = (response or {}).get('result', {}).get('tools')
    if tools is None:
        raise MCPError('MCP 服务 %s 返回缺少 result.tools' % server.get('id'))

    copied_tools = copy.deepcopy(tools)

    if protocol == 'streamable_http':
        with _CACHE_LOCK:
            _TOOLS_CACHE[key] = {
                'tools': copy.deepcopy(copied_tools),
                'expires_at': now + _TOOLS_TTL,
            }
            _prune_cache(_TOOLS_CACHE, now)

    return copy.deepcopy(copied_tools)

def _list_tools_details_with_errors():
    tools_out = []
    errors = []

    for server in servers():
        if not server.get('enabled', True) or not server.get('url'):
            continue

        server_id = server.get('id')
        if not server_id:
            continue
        # 命名空间分隔符不能出现在 server_id 里，否则 _parse_tool_name 会串线。
        safe_id = str(server_id).replace(_NS_SEP, '_')

        try:
            raw_tools = _get_server_tools(server)
        except Exception as exc:
            errors.append(
                '%s [%s]: %s' % (
                    server.get('name') or server_id or 'MCP',
                    server.get('url'),
                    exc,
                )
            )
            continue

        for tool in raw_tools:
            original_name = tool.get('name')
            if not original_name:
                continue
            tools_out.append(dict(
                tool,
                name='%s%s%s' % (safe_id, _NS_SEP, original_name),
                _mcp_server=server_id,
                _original_name=original_name,
                _mcp_url=server.get('url'),
            ))

    return tools_out, errors

def list_tools_details():
    return _list_tools_details_with_errors()

def list_tools():
    return list_tools_details()[0]

def availability_warning():
    tools,errors=list_tools_details()
    if errors:
        return '当前MCP不可用：' + '；'.join(errors)
    return ''

def user_connection_warning():
    warning=availability_warning()
    if not warning:
        return ''
    detail=warning.split('：',1)[1] if '：' in warning else warning
    return '当前无法连接MCP服务器，错误代码：%s。如果不需要MCP，请暂时禁用该服务器。' % detail
def test_connection(server_id=None):
    selected=[s for s in servers() if s.get('enabled',True) and s.get('url') and (server_id is None or s.get('id')==server_id)]
    if not selected: return {'ok':False,'error':'没有启用的 MCP 服务'}
    results=[]
    for s in selected:
        try:
            result=_request(s,'tools/list'); tools=result.get('result',{}).get('tools',[])
            results.append({'id':s.get('id'),'name':s.get('name'),'url':s.get('url'),'protocol':_proto(s),'ok':True,'tool_count':len(tools),'tools':[t.get('name') for t in tools]})
        except Exception as exc:
            results.append({'id':s.get('id'),'name':s.get('name'),'url':s.get('url'),'protocol':_proto(s),'ok':False,'error':str(exc)})
    return {'ok':any(x.get('ok') for x in results),'results':results}

def diagnostics():
    tools,errors=list_tools_details()
    return {'servers':[{'id':s.get('id'),'name':s.get('name'),'url':s.get('url'),'protocol':_proto(s),'enabled':s.get('enabled',True)} for s in servers()], 'tool_count':len(tools), 'tools':[t.get('name') for t in tools], 'errors':errors}

def _parse_tool_name(name):
    if not isinstance(name, str) or _NS_SEP not in name:
        return None, None
    # 按 server_id 最长前缀匹配，而不是盲切第一个 '__'，
    # 避免 server_id 本身包含 '__' 时路由到错误服务。
    best_id = None
    known = [
        str(item.get('id')) for item in servers()
        if item.get('id')
    ]
    for candidate in known:
        prefix = candidate + _NS_SEP
        if name.startswith(prefix) and name != prefix:
            if best_id is None or len(candidate) > len(best_id):
                best_id = candidate
    if best_id is not None:
        return best_id, name[len(best_id) + len(_NS_SEP):]
    server_id, original_name = name.split(_NS_SEP, 1)
    return server_id, original_name

def _find_tool(server, original_name, force_refresh=False):
    tools = _get_server_tools(server, force_refresh=force_refresh)
    for tool in tools:
        if tool.get('name') == original_name:
            return tool
    return None

def _validate_args(tool, args):
    schema = tool.get('inputSchema')
    if not schema:
        return [], None
    if not _HAS_JSONSCHEMA:
        return None, "缺少依赖 jsonschema，请执行: pip install 'jsonschema>=3.2,<4.18'"
    try:
        validator_class = _js_validators.validator_for(schema, default=Draft7Validator)
        validator_class.check_schema(schema)
        validator = validator_class(schema)
        errors = []
        for error in sorted(validator.iter_errors(args), key=lambda item: list(item.path)):
            parts = list(error.path)
            # required 校验失败时 error.path 指向父对象，缺失字段名只出现在
            # message 里（如 "'path' is a required property"），补进 path 更好定位。
            if error.validator == 'required' and isinstance(args, dict):
                for missing in error.validator_value or []:
                    if missing not in args:
                        parts.append(missing)
                        break
            path = '.'.join(str(item) for item in parts) or '(root)'
            errors.append({'path': path, 'message': error.message})
        return errors, None
    except Exception as exc:
        return None, '工具 %s 的 inputSchema 无法解析: %s' % (tool.get('name'), exc)

def _tool_error(text, details=None):
    return {
        'content': [{'type': 'text', 'text': text}],
        'isError': True,
        'details': details or [],
    }

def call_tool(name, args):
    server_id, original_name = _parse_tool_name(name)
    if not server_id:
        return _tool_error('工具名缺少服务前缀（%s）: %s' % (_NS_SEP, name))

    server = next((item for item in servers() if item.get('id') == server_id), None)
    if not server:
        return _tool_error('未知的 MCP 服务: %s' % server_id)

    try:
        tool = _find_tool(server, original_name)
        if tool is None:
            tool = _find_tool(server, original_name, force_refresh=True)
    except MCPError as exc:
        return _tool_error('当前MCP不可用：%s' % exc)

    if tool is None:
        return _tool_error('服务 %s 不存在工具 %s' % (server_id, original_name))

    errors, schema_error = _validate_args(tool, args)
    if schema_error:
        return _tool_error(schema_error, details=[{'path': '(schema)', 'message': schema_error}])
    if errors:
        return _tool_error('参数校验失败，请根据 details 修正后重试', details=errors)

    response = _request(server, 'tools/call', {'name': original_name, 'arguments': args})

    if isinstance(response, dict) and response.get('error'):
        return _tool_error('工具调用失败: %s' % json.dumps(response['error'], ensure_ascii=False))

    return response.get('result', {})
