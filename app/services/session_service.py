import uuid, datetime
from .json_store import data, save_data
def now(): return datetime.datetime.utcnow().replace(microsecond=0).isoformat()+'Z'
def all_sessions(): return data('sessions.json', [])
def messages(): return data('messages.json', [])
def create(owner_id, title='新会话'):
 s={'id':uuid.uuid4().hex,'owner_id':owner_id,'title':title,'created_at':now(),'updated_at':now()}; a=all_sessions(); a.append(s); save_data('sessions.json',a); return s
def owned(owner_id,sid): return next((s for s in all_sessions() if s['id']==sid and s['owner_id']==owner_id),None)
def list_owned(owner_id): return [s for s in all_sessions() if s['owner_id']==owner_id]
def get_messages(sid): return [m for m in messages() if m['session_id']==sid]
def add_message(sid,role,content,meta=None):
 m={'id':uuid.uuid4().hex,'session_id':sid,'role':role,'content':content,'meta':meta or {},'created_at':now()}; a=messages(); a.append(m); save_data('messages.json',a); return m
