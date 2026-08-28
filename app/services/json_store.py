import json,os,threading,tempfile
from app.config import CONFIG_DIR,DATA_DIR
L=threading.RLock()
def ensure_dirs(): os.makedirs(CONFIG_DIR,exist_ok=True); os.makedirs(DATA_DIR,exist_ok=True)
def read(p,d):
 ensure_dirs()
 with L:
  try:
   with open(p,encoding="utf-8") as f:return json.load(f)
  except (OSError,ValueError):return d
def write(p,x):
 ensure_dirs(); fd,t=tempfile.mkstemp(dir=os.path.dirname(p));
 with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(x,f,ensure_ascii=False,indent=2)
 os.replace(t,p)
def data(n,d):return read(os.path.join(DATA_DIR,n),d)
def save_data(n,x):write(os.path.join(DATA_DIR,n),x)
def config(n,d):return read(os.path.join(CONFIG_DIR,n),d)
def save_config(n,x):write(os.path.join(CONFIG_DIR,n),x)
