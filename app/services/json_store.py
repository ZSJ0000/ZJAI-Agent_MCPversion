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
 ensure_dirs()
 # 写临时文件 + os.replace 必须在锁内完成，否则两个请求并发保存时
 # 会各自读到旧快照再整体覆盖，造成丢更新。
 with L:
  fd,t=tempfile.mkstemp(dir=os.path.dirname(p))
  try:
   with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(x,f,ensure_ascii=False,indent=2)
   os.replace(t,p)
  except BaseException:
   try: os.unlink(t)
   except OSError: pass
   raise
def data(n,d):return read(os.path.join(DATA_DIR,n),d)
def save_data(n,x):write(os.path.join(DATA_DIR,n),x)
def config(n,d):return read(os.path.join(CONFIG_DIR,n),d)
def save_config(n,x):write(os.path.join(CONFIG_DIR,n),x)
