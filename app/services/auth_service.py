import os
from werkzeug.security import generate_password_hash, check_password_hash
from .json_store import data, save_data
from app.config import DATA_DIR
def users(): return data('users.json', [])
def initialized(): return os.path.exists(os.path.join(DATA_DIR, 'install.lock'))
def create_user(name, password, role='user'):
    name=(name or '').strip()
    if not name or len(password or '') < 6: raise ValueError('用户名不能为空，密码至少 6 位')
    a=users()
    if any(x.get('username')==name for x in a): raise ValueError('用户名已存在')
    u={'id':str(max([int(x.get('id',0)) for x in a] or [0])+1),'username':name,'password_hash':generate_password_hash(password),'role':role,'enabled':True}
    a.append(u); save_data('users.json',a); return u
def authenticate(name,password):
    for u in users():
        if u.get('username')==name and u.get('enabled',True) and check_password_hash(u.get('password_hash',''),password or ''): return u
    return None
def public_user(u): return {k:u.get(k) for k in ('id','username','role','enabled')}
def set_enabled(user_id, enabled):
    a=users()
    for u in a:
        if u.get('id')==str(user_id): u['enabled']=bool(enabled); save_data('users.json',a); return public_user(u)
    raise ValueError('用户不存在')
