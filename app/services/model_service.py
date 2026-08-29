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
from .mcp_service import list_tools, call_tool
from .skill_service import load_skills
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
    return found

def _clean_tool_markup(value):
    value=str(value or '')
    for pattern in (r'<tool_call>.*?</tool_call>', r'<\|tool_call_start\|>.*?<\|tool_call_end\|>', r'<tool_call_start>.*?<tool_call_end>'):
        value=re.sub(pattern,'',value,flags=re.S)
    return value.strip()

def models(): return config('models.json', {'models':[]}).get('models',[])
def _model(model_id):
    m=next((x for x in models() if x.get('id')==model_id and x.get('enabled',True)),None)
    if not m: raise ValueError('模型不存在或未启用')
    return m
def _body(m,messages,stream=False):
    ts=list_tools(); skills='\n\n'.join(x['content'] for x in load_skills()); all_messages=messages
    if skills: all_messages=[{'role':'system','content':skills}]+all_messages
    b={'model':m['model'],'messages':all_messages,'temperature':m.get('temperature',.7),'max_tokens':m.get('max_tokens',4096)}
    if stream:b['stream']=True
    if ts:b['tools']=[{'type':'function','function':{'name':t['name'],'description':t.get('description',''),'parameters':t.get('inputSchema',{})}} for t in ts]
    return b
def run(model_id,messages):
    m=_model(model_id); body=_body(m,messages); headers={'Authorization':'Bearer '+m.get('api_key',''),'Content-Type':'application/json'}
    for _ in range(5):
        r=requests.post(m['base_url'].rstrip('/')+'/chat/completions',json=body,headers=headers,timeout=120);r.raise_for_status();msg=r.json()['choices'][0]['message'];body['messages'].append(msg);calls=_extract_tool_calls(msg)
        if not calls:
            if msg.get('content'): msg['content']=_clean_tool_markup(msg.get('content'))
            return msg
        for c in calls:
            result=call_tool(c['function']['name'],json.loads(c['function'].get('arguments') or '{}'));body['messages'].append({'role':'tool','tool_call_id':c.get('id',''),'name':c['function']['name'],'content':json.dumps(result,ensure_ascii=False)})
    raise ValueError('工具调用轮次超过限制')
def stream_reply(model_id, messages):
    if list_tools(): return None
    m=_model(model_id); body=_body(m,messages,True)
    headers={'Authorization':'Bearer '+m.get('api_key',''),'Content-Type':'application/json','Accept':'text/event-stream','Accept-Charset':'utf-8'}
    response=requests.post(m['base_url'].rstrip('/')+'/chat/completions',json=body,headers=headers,timeout=120,stream=True)
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

