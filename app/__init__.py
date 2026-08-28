from flask import Flask,redirect,session,request,jsonify,render_template
from .config import SECRET_KEY
from .services.json_store import ensure_dirs,save_config
from .services.auth_service import initialized,authenticate,create_user
from .services.model_service import models,chat
from .services.skill_service import load_skills
from .services.mcp_service import tools
def create_app():
 app=Flask(__name__,template_folder="../templates",static_folder="../static");app.secret_key=SECRET_KEY;ensure_dirs()
 @app.before_request
 def gate():
  if not initialized() and request.path not in ("/setup","/login") and not request.path.startswith("/static"):return redirect("/setup")
 @app.route("/")
 def index():return redirect("/chat" if session.get("user") else "/login")
 @app.route("/setup",methods=["GET","POST"])
 def setup():
  if initialized():return render_template("message.html",title="系统已初始化",message="请返回登录页面。")
  if request.method=="POST":
   u=request.form.get("username","").strip();p=request.form.get("password","");c=request.form.get("confirm","")
   if not u or len(p)<6 or p!=c:return render_template("setup.html",error="账号不能为空，密码至少6位且必须一致")
   create_user(u,p,"admin");save_config("app.json",{"name":"ZJAI-Agent_MCPversion","initialized":True});save_config("models.json",{"models":[]});save_config("mcp.json",{"servers":[]});return redirect("/login")
  return render_template("setup.html")
 @app.route("/login",methods=["GET","POST"])
 def login():
  if request.method=="POST":
   u=authenticate(request.form.get("username",""),request.form.get("password",""))
   if u:session["user"]={"id":u["id"],"username":u["username"],"role":u["role"]};return redirect("/chat")
   return render_template("login.html",error="账号或密码错误")
  return render_template("login.html")
 @app.post("/logout")
 def logout():session.clear();return redirect("/login")
 @app.get("/chat")
 def page():return render_template("chat.html",user=session["user"]) if session.get("user") else redirect("/login")
 @app.get("/api/models")
 def api_models():return jsonify(models=[{"id":m.get("id"),"name":m.get("name"),"default":m.get("default",False)} for m in models()])
 @app.get("/api/skills")
 def api_skills():return jsonify(skills=load_skills())
 @app.get("/api/mcp/tools")
 def api_tools():return jsonify(tools=tools())
 @app.post("/api/chat")
 def api_chat():
  if not session.get("user"):return jsonify(error="未登录"),401
  x=request.get_json() or {}
  try:return jsonify(message=chat(x.get("model_id"),[{"role":"user","content":x.get("message","")}]))
  except Exception as e:return jsonify(error=str(e)),502
 @app.get("/AdminX")
 def admin():return render_template("admin.html",user=session["user"]) if session.get("user",{}).get("role")=="admin" else ("无权限",403)
 return app
