import json, requests
from .json_store import config
class MCPError(Exception): pass
def servers(): return config('mcp.json',{'servers':[]}).get('servers',[])
def _headers(s):
    h=dict(s.get('headers') or {}); h.setdefault('Accept','application/json, text/event-stream'); return h
def _sse_endpoint(s,h):
    r=requests.get(s['url'],headers=h,stream=True,timeout=30,verify=s.get('verify_ssl',True)); r.raise_for_status()
    for raw in r.iter_lines(decode_unicode=True):
        line=(raw or '').strip()
        if line.startswith('data:') and line[5:].strip(): return line[5:].strip()
    raise MCPError('SSE 未返回 endpoint')
def rpc(s, method, params=None):
    h=_headers(s); payload={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}}
    if s.get('protocol','streamable_http')=='sse': url=_sse_endpoint(s,h)
    else: url=s['url']
    r=requests.post(url,json=payload,headers=h,timeout=60,verify=s.get('verify_ssl',True)); r.raise_for_status()
    text=r.text.strip()
    if text.startswith('data:'): text=text[5:].strip()
    try:return r.json() if text==r.text.strip() else json.loads(text)
    except ValueError: raise MCPError('MCP 返回不是 JSON')
def list_tools():
    out=[]
    for s in servers():
        if not s.get('enabled',True): continue
        try:
            for t in rpc(s,'tools/list').get('result',{}).get('tools',[]): out.append(dict(t,_mcp_server=s.get('id')))
        except Exception: continue
    return out
def call_tool(name,args):
    for s in servers():
        if not s.get('enabled',True): continue
        try:
            ts=rpc(s,'tools/list').get('result',{}).get('tools',[])
            if any(t.get('name')==name for t in ts): return rpc(s,'tools/call',{'name':name,'arguments':args}).get('result',{})
        except Exception: continue
    raise MCPError('MCP 工具不存在或调用失败: '+name)
