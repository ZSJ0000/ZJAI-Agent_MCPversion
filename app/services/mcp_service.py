import json, requests, uuid
from urllib.parse import urljoin
from .json_store import config
class MCPError(Exception): pass

def servers(): return config('mcp.json',{'servers':[]}).get('servers',[])

def _headers(s):
    h=dict(s.get('headers') or {})
    if not s.get('oauth_enabled', bool(h.get('Authorization'))):
        h.pop('Authorization',None)
    h.setdefault('Accept','application/json, text/event-stream')
    return h

def _sse_endpoint(s,h):
    r=requests.get(s['url'],headers=h,stream=True,timeout=30,verify=s.get('verify_ssl',True)); r.raise_for_status()
    for raw in r.iter_lines(decode_unicode=True):
        line=(raw or '').strip()
        if line.startswith('data:') and line[5:].strip(): return urljoin(s['url'], line[5:].strip())
    raise MCPError('SSE 未返回 endpoint')

def _json_response(r):
    text=r.text.strip()
    if text.startswith('data:'): text=text[5:].strip()
    try: return r.json() if text==r.text.strip() else json.loads(text)
    except ValueError: raise MCPError('MCP 返回不是 JSON')

def _post(url, payload, h, s):
    r=requests.post(url,json=payload,headers=h,timeout=60,verify=s.get('verify_ssl',True)); r.raise_for_status()
    sid=r.headers.get('Mcp-Session-Id') or r.headers.get('mcp-session-id')
    return _json_response(r), sid

def _request(s, method, params=None):
    h=_headers(s); payload={'jsonrpc':'2.0','id':str(uuid.uuid4()),'method':method,'params':params or {}}
    protocol=s.get('protocol','streamable_http')
    if protocol=='sse':
        return _post(_sse_endpoint(s,h),payload,h,s)[0]
    # MCP Streamable HTTP requires initialize before tools/list/tools/call.
    init, sid=_post(s['url'], {'jsonrpc':'2.0','id':payload['id'],'method':'initialize','params':{
        'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'ZJAI-Agent','version':'1.0'}
    }}, h, s)
    if sid: h['Mcp-Session-Id']=sid
    if init.get('error'): raise MCPError(str(init['error']))
    notice={'jsonrpc':'2.0','method':'notifications/initialized','params':{}}
    try: requests.post(s['url'],json=notice,headers=h,timeout=30,verify=s.get('verify_ssl',True))
    except requests.RequestException: pass
    result,_=_post(s['url'],payload,h,s)
    return result

def list_tools():
    out=[]; errors=[]
    for s in servers():
        if not s.get('enabled',True) or not s.get('url'): continue
        try:
            result=_request(s,'tools/list')
            if result.get('error'): raise MCPError(str(result['error']))
            for t in result.get('result',{}).get('tools',[]): out.append(dict(t,_mcp_server=s.get('id')))
        except Exception as exc: errors.append('%s: %s'%(s.get('name') or s.get('id') or 'MCP',exc))
    return out

def call_tool(name,args):
    for s in servers():
        if not s.get('enabled',True) or not s.get('url'): continue
        try:
            result=_request(s,'tools/list')
            ts=result.get('result',{}).get('tools',[])
            if any(t.get('name')==name for t in ts):
                result=_request(s,'tools/call',{'name':name,'arguments':args})
                if result.get('error'): raise MCPError(str(result['error']))
                return result.get('result',{})
        except Exception: continue
    raise MCPError('MCP 工具不存在或调用失败: '+name)
