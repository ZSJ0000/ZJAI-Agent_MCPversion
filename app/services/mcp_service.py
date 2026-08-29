import json, requests, uuid
from urllib.parse import urljoin
from .json_store import config
class MCPError(Exception): pass

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
        raise MCPError('请求 %s 返回 HTTP %s: %s' % (url,r.status_code,r.text[:300]))
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

def _request(s,method,params=None):
    h=_headers(s); request_id=str(uuid.uuid4()); payload={'jsonrpc':'2.0','id':request_id,'method':method,'params':params or {}}
    protocol=s.get('protocol','streamable_http')
    if protocol=='sse': return _post(_sse_endpoint(s,h),payload,h,s)[0]
    init_payload={'jsonrpc':'2.0','id':request_id,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'ZJAI-Agent','version':'1.0'}}}
    init,sid=_post(s['url'],init_payload,h,s)
    if sid: h['Mcp-Session-Id']=sid
    if init.get('error'): raise MCPError('initialize 失败: '+json.dumps(init['error'],ensure_ascii=False))
    try: _post(s['url'],{'jsonrpc':'2.0','method':'notifications/initialized','params':{}},h,s,False)
    except MCPError: pass
    result,_=_post(s['url'],payload,h,s)
    return result

def list_tools_details():
    out=[]; errors=[]
    for s in servers():
        if not s.get('enabled',True) or not s.get('url'): continue
        try:
            result=_request(s,'tools/list')
            if result.get('error'): raise MCPError(json.dumps(result['error'],ensure_ascii=False))
            tools=result.get('result',{}).get('tools',[])
            for t in tools: out.append(dict(t,_mcp_server=s.get('id'),_mcp_url=s.get('url')))
        except Exception as exc: errors.append('%s [%s]: %s'%(s.get('name') or s.get('id') or 'MCP',s.get('url'),exc))
    return out,errors

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
            results.append({'id':s.get('id'),'name':s.get('name'),'url':s.get('url'),'protocol':s.get('protocol'),'ok':True,'tool_count':len(tools),'tools':[t.get('name') for t in tools]})
        except Exception as exc:
            results.append({'id':s.get('id'),'name':s.get('name'),'url':s.get('url'),'protocol':s.get('protocol'),'ok':False,'error':str(exc)})
    return {'ok':any(x.get('ok') for x in results),'results':results}

def diagnostics():
    tools,errors=list_tools_details()
    return {'servers':[{'id':s.get('id'),'name':s.get('name'),'url':s.get('url'),'protocol':s.get('protocol'),'enabled':s.get('enabled',True)} for s in servers()], 'tool_count':len(tools), 'tools':[t.get('name') for t in tools], 'errors':errors}

def call_tool(name,args):
    tools,errors=list_tools_details()
    matches=[t for t in tools if t.get('name')==name]
    if not matches:
        detail='；'.join(errors) if errors else '当前工具列表中不存在该工具'
        return {'content':[{'type':'text','text':'当前MCP不可用：'+detail}], 'isError':True}
    for s in servers():
        if not s.get('enabled',True) or not s.get('url') or s.get('id')!=matches[0].get('_mcp_server'): continue
        result=_request(s,'tools/call',{'name':name,'arguments':args})
        if result.get('error'): raise MCPError('工具调用失败: '+json.dumps(result['error'],ensure_ascii=False))
        return result.get('result',{})
    return {'content':[{'type':'text','text':'当前MCP不可用：MCP 服务不存在: '+name}], 'isError':True}
