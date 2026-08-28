from werkzeug.security import generate_password_hash,check_password_hash
from .json_store import data,save_data
def users():return data("users.json",[])
def initialized():return bool(users())
def create_user(name,password,role="user"):
 a=users();u={"id":str(len(a)+1),"username":name,"password_hash":generate_password_hash(password),"role":role,"enabled":True};a.append(u);save_data("users.json",a);return u
def authenticate(name,password):
 for u in users():
  if u["username"]==name and u.get("enabled",True) and check_password_hash(u["password_hash"],password):return u
