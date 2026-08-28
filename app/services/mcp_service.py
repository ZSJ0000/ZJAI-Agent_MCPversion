import requests
from .json_store import config
def servers():return config("mcp.json",{"servers":[]}).get("servers",[])
def tools():
 out=[]
 for s in servers():
  if not s.get("enabled",True):continue
  h=dict(s.get("headers") or {});h.setdefault("Accept","application/json, text/event-stream")
  try:
   r=requests.post(s["url"],json={"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}},headers=h,timeout=30,verify=s.get("verify_ssl",True));r.raise_for_status();out+=r.json().get("result",{}).get("tools",[])
  except Exception:pass
 return out
