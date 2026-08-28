import json, requests
from .json_store import config
from .mcp_service import list_tools, call_tool
from .skill_service import load_skills
def models(): return config('models.json', {'models':[]}).get('models',[])
def run(model_id, messages):
 m=next((x for x in models() if x.get('id')==model_id and x.get('enabled',True)),None)
 if not m: raise ValueError('模型不存在或未启用')
 ts=list_tools(); body={'model':m['model'],'messages':messages,'temperature':m.get('temperature',.7),'max_tokens':m.get('max_tokens',4096)}
 skills='\n\n'.join(x['content'] for x in load_skills())
 if skills: body['messages']=[{'role':'system','content':skills}]+body['messages']
 if ts: body['tools']=[{'type':'function','function':{'name':t['name'],'description':t.get('description',''),'parameters':t.get('inputSchema',{})}} for t in ts]
 headers={'Authorization':'Bearer '+m.get('api_key',''),'Content-Type':'application/json'}
 for _ in range(5):
  r=requests.post(m['base_url'].rstrip('/')+'/chat/completions',json=body,headers=headers,timeout=120); r.raise_for_status(); msg=r.json()['choices'][0]['message']; body['messages'].append(msg); calls=msg.get('tool_calls') or []
  if not calls:return msg
  for c in calls:
   result=call_tool(c['function']['name'],json.loads(c['function'].get('arguments') or '{}'))
   body['messages'].append({'role':'tool','tool_call_id':c.get('id',''),'name':c['function']['name'],'content':json.dumps(result,ensure_ascii=False)})
 raise ValueError('工具调用轮次超过限制')
