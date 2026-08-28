import uuid, datetime
from .json_store import data, save_data
def now(): return datetime.datetime.utcnow().replace(microsecond=0).isoformat()+'Z'
def all_sessions(): return data('sessions.json',[])
def messages(): return data('messages.json',[])
def create(owner_id,title='新会话'):
    a=all_sessions(); s={'id':uuid.uuid4().hex,'owner_id':str(owner_id),'title':(title or '新会话')[:100],'created_at':now(),'updated_at':now()}; a.append(s); save_data('sessions.json',a); return s
def owned(owner_id,sid): return next((s for s in all_sessions() if s.get('id')==sid and s.get('owner_id')==str(owner_id)),None)
def list_owned(owner_id): return [s for s in all_sessions() if s.get('owner_id')==str(owner_id)]
def get_messages(sid): return [m for m in messages() if m.get('session_id')==sid]
def add_message(sid,role,content,meta=None):
    a=messages(); text=content or ''
    m={'id':uuid.uuid4().hex,'session_id':sid,'role':role,'content':text,'meta':meta or {},'created_at':now()}; a.append(m)
    sessions=all_sessions()
    for item in sessions:
        if item.get('id')==sid:
            item['updated_at']=m['created_at']
            if role=='user' and (not item.get('title') or item.get('title')=='新会话'):
                item['title']=text.replace('\n',' ').strip()[:40] or '新会话'
            break
    save_data('messages.json',a); save_data('sessions.json',sessions); return m
def delete(owner_id,sid):
    if not owned(owner_id,sid): return False
    save_data('sessions.json',[s for s in all_sessions() if s.get('id')!=sid]); save_data('messages.json',[m for m in messages() if m.get('session_id')!=sid]); return True
