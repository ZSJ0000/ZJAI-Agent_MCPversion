import json, re, requests

def _repair_mojibake(value):
    value=str(value or '')
    # Repair common UTF-8 bytes that were incorrectly decoded as Latin-1/CP1252.
    markers=('Ã','Â','Ä','Å','Æ','Ç','Ð','Ñ','â','ã','ä','å','æ','ç','è','é','ê','ë','ì','í','î','ï','ð','ñ','ò','ó','ô','õ','ö','÷','ø','ù','ú','û','ü','ý','þ')
    if any(mark in value for mark in markers):
        try:
            fixed=value.encode('latin1').decode('utf-8')
            if fixed != value: return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return value
from .json_store import config
from .mcp_service import list_tools, call_tool, availability_warning
from .skill_service import load_skills

API_RETRIES = 3

def _post_with_retry(url, **kwargs):
    last = None
    for attempt in range(API_RETRIES):
        try:
            response = requests.post(url, **kwargs)
            if response.status_code >= 500 and attempt + 1 < API_RETRIES:
                continue
            return response
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last = exc
            if attempt + 1 >= API_RETRIES:
                raise
    if last: raise last

def _json_objects(text):
    """Extract complete balanced JSON objects from model text."""
    result=[]; starts=[]
    for i,ch in enumerate(text):
        if ch=='{': starts.append(i)
        elif ch=='}' and starts:
            begin=starts.pop()
            if not starts:
                candidate=text[begin:i+1]
                try: result.append(json.loads(candidate))
                except (ValueError,TypeError): pass
    return result

def _extract_tool_calls(message):
    calls=message.get('tool_calls') or []
    if calls: return calls
    content=message.get('content') or ''
    regions=[]
    for left,right in (
        ('<tool_call>','</tool_call>'),
        ('<|tool_call_start|>','<|tool_call_end|>'),
        ('<tool_call_start>','<tool_call_end>'),
    ):
        pos=0
        while True:
            a=content.find(left,pos)
            if a<0: break
            a+=len(left); b=content.find(right,a)
            if b<0: break
            regions.append(content[a:b]); pos=b+len(right)
    found=[]
    for region in regions:
        for item in _json_objects(region):
            if not isinstance(item,dict): continue
            fn=item.get('function') or {}
            name=item.get('name') or fn.get('name')
            args=item.get('arguments',item.get('parameters',fn.get('arguments',{})))
            if isinstance(args,str):
                try: args=json.loads(args or '{}')
                except (ValueError,TypeError): continue
            if name:
                found.append({'id':'text-call-'+str(len(found)+1),'type':'function','function':{'name':name,'arguments':json.dumps(args or {},ensure_ascii=False)}})
    # Some local models emit a function-style call instead of tool-call tokens:
    # mcp_filesystem_create_file({"projectId":"default", ...})
    # Extract the balanced JSON after the function name, including nested values.
    for match in re.finditer(r'([A-Za-z_][\w.-]*)\s*\(', content):
        name=match.group(1)
        if name in {c.get('function',{}).get('name') for c in found}: continue
        begin=match.end(); depth=1; end=None
        for pos in range(begin,len(content)):
            if content[pos]=='(': depth+=1
            elif content[pos]==')':
                depth-=1
                if depth==0: end=pos; break
        if end is None: continue
        for item in _json_objects(content[begin:end]):
            if isinstance(item,dict):
                found.append({'id':'text-call-'+str(len(found)+1),'type':'function','function':{'name':name,'arguments':json.dumps(item,ensure_ascii=False)}})
                break
    return found

def _normalize_tool_message(message, calls):
    if not calls: return message
    normalized={'role':'assistant','content':None,'tool_calls':calls}
    return normalized

def _clean_tool_markup(value):
    value=str(value or '')
    for pattern in (r'<tool_call>.*?</tool_call>', r'<\|tool_call_start\|>.*?<\|tool_call_end\|>', r'<tool_call_start>.*?<tool_call_end>', r'[A-Za-z_][\w.-]*\s*\(\s*\{.*?\}\s*\)'):
        value=re.sub(pattern,'',value,flags=re.S)
    return value.strip()

def models(): return config('models.json', {'models':[]}).get('models',[])
def _model(model_id):
    m=next((x for x in models() if x.get('id')==model_id and x.get('enabled',True)),None)
    if not m: raise ValueError('模型不存在或未启用')
    return m
def _mcp_instruction(tools):
    if not tools:
        return "当前没有可用的 MCP 工具。不要编造或输出任何工具调用。"
    lines=[]
    for t in tools:
        schema=t.get("inputSchema") or {}
        props=schema.get("properties") or {}
        required=schema.get("required") or []
        args=[]
        for key,value in props.items():
            desc=value.get("description","") if isinstance(value,dict) else ""
            args.append("%s%s%s" % (key, "(必填)" if key in required else "(可选)", ": "+desc if desc else ""))
        lines.append("- %s: %s%s" % (t.get("name"), t.get("description",""), ("；参数："+"，".join(args)) if args else ""))
    return """你是一个可以调用 MCP 工具的助手。

当前真实可用的 MCP 工具：
%s

工具调用规则（必须遵守）：
1. 只能调用上面列出的真实工具，严禁编造工具名称。
2. 需要执行工具时，必须只输出一条完整的工具调用，不要先说“我正在调用”。
3. 工具调用必须严格使用以下格式，JSON 必须完整且可解析：
<tool_call>{\"name\":\"工具真实名称\",\"arguments\":{\"参数名\":\"参数值\"}}</tool_call>
4. arguments 必须是 JSON 对象，并且严格符合该工具的参数要求。
5. 不要输出 mcp_xxx({...})、Python 函数调用、Markdown 代码块或不完整 JSON。
6. 工具执行结果返回后，先检查用户任务是否全部完成；如果还有任何步骤未完成，必须继续输出下一条工具调用，不要提前结束。
7. 只有全部步骤完成后，才用中文向用户说明结果；不要把“操作成功”“执行完成”等状态词当作任务完成依据。
8. 如果一次任务需要多个工具，应按实际依赖连续调用工具，例如先检查/读取，再创建/修改/删除，必要时再验证。
9. 每次回复最多只能请求一个工具。工具返回后，再根据结果输出下一段说明或请求下一个工具，禁止一次回复请求多个工具。
""" % "\n".join(lines)

def _body(m,messages,stream=False):
    ts=list_tools()
    warning=availability_warning()
    skills="\n\n".join(x["content"] for x in load_skills())
    instruction=_mcp_instruction(ts)
    if warning: instruction += '\n\n' + warning + '\n请不要假装调用不可用的 MCP 工具；如用户的问题不依赖 MCP，请继续正常回答。'
    system="\n\n".join([instruction,skills] if skills else [instruction])
    all_messages=[{"role":"system","content":system}]+messages
    b={"model":m["model"],"messages":all_messages,"temperature":m.get("temperature",.7),"max_tokens":m.get("max_tokens",4096)}
    if stream:b["stream"]=True
    if ts:b["tools"]=[{"type":"function","function":{"name":t["name"],"description":t.get("description",""),"parameters":t.get("inputSchema",{})}} for t in ts]
    return b

def run(model_id,messages,on_tool=None):
    m=_model(model_id); body=_body(m,messages); headers={'Authorization':'Bearer '+m.get('api_key',''),'Content-Type':'application/json'}
    # Allow chained tool calls while retaining a hard safety limit.
    for _ in range(12):
        try:
            r=_post_with_retry(m['base_url'].rstrip('/')+'/chat/completions',json=body,headers=headers,timeout=(15,120))
            r.raise_for_status(); data=r.json(); msg=data['choices'][0]['message']
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            if isinstance(exc, requests.exceptions.ConnectTimeout):
                raise ValueError('模型连接超时：无法连接模型服务')
            if isinstance(exc, requests.exceptions.ReadTimeout):
                raise ValueError('模型响应超时：模型服务未及时返回')
            if isinstance(exc, requests.exceptions.ConnectionError):
                raise ValueError('模型连接失败：'+str(exc))
            raise ValueError('模型响应无效: '+str(exc))
        calls=_extract_tool_calls(msg)
        if calls:
            body['messages'].append(_normalize_tool_message(msg,calls))
        else:
            body['messages'].append(msg)
        if not calls:
            if msg.get('content'): msg['content']=_clean_tool_markup(msg.get('content'))
            return msg
        for c in calls:
            tool_name=c.get('function',{}).get('name','')
            try: tool_args=json.loads(c.get('function',{}).get('arguments') or '{}')
            except (ValueError,TypeError): tool_args={}
            if on_tool: on_tool('start',tool_name,tool_args)
            try:
                result=call_tool(tool_name,tool_args)
            except Exception as exc:
                result={'content':[{'type':'text','text':'当前MCP不可用：'+str(exc)}], 'isError':True}
            if on_tool: on_tool('result',tool_name,result)
            body['messages'].append({'role':'tool','tool_call_id':c.get('id',''),'name':tool_name,'content':json.dumps(result,ensure_ascii=False)})
    raise ValueError('工具调用轮次超过限制')
def stream_reply(model_id, messages):
    if list_tools(): return None
    m=_model(model_id); body=_body(m,messages,True)
    headers={'Authorization':'Bearer '+m.get('api_key',''),'Content-Type':'application/json','Accept':'text/event-stream','Accept-Charset':'utf-8'}
    response=_post_with_retry(m['base_url'].rstrip('/')+'/chat/completions',json=body,headers=headers,timeout=120,stream=True)
    response.raise_for_status(); response.encoding='utf-8'
    def chunks():
        for raw in response.iter_lines(decode_unicode=True):
            line=(raw or '').strip()
            if not line or line.startswith(':'): continue
            if line.startswith('data:'): line=line[5:].strip()
            if line=='[DONE]': break
            try:
                value=json.loads(line).get('choices',[{}])[0].get('delta',{}).get('content')
                if value: yield _repair_mojibake(value)
            except (ValueError,TypeError,IndexError,KeyError): continue
    return chunks()



def agent_stream(model_id, messages):
    """Yield Agent events in execution order: plan, tools, then final answer."""
    m=_model(model_id); body=_body(m,messages); headers={'Authorization':'Bearer '+m.get('api_key',''),'Content-Type':'application/json'}
    yield {'type':'plan','content':'分析任务并按需要执行工具'}
    for _ in range(12):
        try:
            r=_post_with_retry(m['base_url'].rstrip('/')+'/chat/completions',json=body,headers=headers,timeout=(15,120))
            r.raise_for_status(); msg=r.json()['choices'][0]['message']
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            if isinstance(exc, requests.exceptions.ConnectTimeout): raise ValueError('模型连接超时：无法连接模型服务')
            if isinstance(exc, requests.exceptions.ReadTimeout): raise ValueError('模型响应超时：模型服务未及时返回')
            if isinstance(exc, requests.exceptions.ConnectionError): raise ValueError('模型连接失败：'+str(exc))
            raise ValueError('模型响应无效: '+str(exc))
        calls=_extract_tool_calls(msg)
        # Preserve the model's progress narration before the tool card.
        # This enables the natural Agent sequence: text -> tool -> text -> tool.
        if calls:
            progress=_clean_tool_markup(msg.get('content','') or '')
            if progress and progress not in ('操作成功','操作完成','执行成功','已完成操作'):
                yield {'type':'progress','content':progress}
        if not calls:
            answer=dict(msg)
            answer['content']=_clean_tool_markup(answer.get('content','') or '')
            if answer.get('content','').strip() in ('操作成功','操作完成','执行成功','已完成操作'): answer['content']=''
            yield {'type':'final','content':answer.get('content','') or ''}
            return
        body['messages'].append(_normalize_tool_message(msg,calls))
        for c in calls:
            name=c.get('function',{}).get('name','')
            try: args=json.loads(c.get('function',{}).get('arguments') or '{}')
            except (ValueError,TypeError): args={}
            yield {'type':'tool_start','name':name or 'tool','data':args}
            try: result=call_tool(name,args)
            except Exception as exc: result={'content':[{'type':'text','text':'当前MCP不可用：'+str(exc)}], 'isError':True}
            yield {'type':'tool_result','name':name or 'tool','data':result}
            body['messages'].append({'role':'tool','tool_call_id':c.get('id',''),'name':name,'content':json.dumps(result,ensure_ascii=False)})
    raise ValueError('工具调用轮次超过限制')
