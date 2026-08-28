import json, requests

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
        r=requests.post(m['base_url'].rstrip('/')+'/chat/completions',json=body,headers=headers,timeout=120);r.raise_for_status();msg=r.json()['choices'][0]['message'];body['messages'].append(msg);calls=msg.get('tool_calls') or []
        if not calls:return msg
        for c in calls:
            result=call_tool(c['function']['name'],json.loads(c['function'].get('arguments') or '{}'));body['messages'].append({'role':'tool','tool_call_id':c.get('id',''),'name':c['function']['name'],'content':json.dumps(result,ensure_ascii=False)})
    raise ValueError('工具调用轮次超过限制')
def stream_reply(model_id, messages):
    if list_tools(): return None
    m=_model(model_id); body=_body(m,messages,True)
    headers={'Authorization':'Bearer '+m.get('api_key',''),'Content-Type':'application/json','Accept':'text/event-stream'}
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

