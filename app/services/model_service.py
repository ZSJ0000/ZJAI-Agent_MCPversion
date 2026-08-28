import requests
from .json_store import config
def models():return config("models.json",{"models":[]}).get("models",[])
def chat(mid,msgs):
 m=next((x for x in models() if x.get("id")==mid and x.get("enabled",True)),None)
 if not m:raise ValueError("模型不存在或未启用")
 r=requests.post(m["base_url"].rstrip("/")+"/chat/completions",json={"model":m["model"],"messages":msgs,"temperature":m.get("temperature",.7),"max_tokens":m.get("max_tokens",4096)},headers={"Authorization":"Bearer "+m.get("api_key","")},timeout=120);r.raise_for_status();return r.json()["choices"][0]["message"]
