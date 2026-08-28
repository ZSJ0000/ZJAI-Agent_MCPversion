from flask import Flask, redirect, session, request, jsonify, render_template
from .config import SECRET_KEY
from .services.json_store import ensure_dirs, save_config, config
from .services.auth_service import initialized, authenticate, create_user, users, public_user, set_enabled
from .services.model_service import models, run
from .services.skill_service import load_skills
from .services.mcp_service import list_tools
from .services.session_service import create, owned, list_owned, get_messages, add_message, delete

def create_app():
    app=Flask(__name__,template_folder='../templates',static_folder='../static'); app.secret_key=SECRET_KEY; ensure_dirs()
    @app.before_request
    def gate():
        if not initialized() and request.path not in ('/setup','/login') and not request.path.startswith('/static'): return redirect('/setup')
    def uid(): return session.get('user',{}).get('id')
    def admin(): return session.get('user',{}).get('role')=='admin'
    @app.route('/')
    def index(): return redirect('/chat' if uid() else '/login')
    @app.route('/setup',methods=['GET','POST'])
    def setup():
        if initialized(): return render_template('message.html',title='系统已初始化',message='请返回登录页面。')
        if request.method=='POST':
            u=request.form.get('username','').strip(); p=request.form.get('password',''); c=request.form.get('confirm','')
            if not u or len(p)<6 or p!=c:return render_template('setup.html',error='账号不能为空，密码至少6位且两次必须一致')
            create_user(u,p,'admin'); save_config('app.json',{'name':'ZJAI-Agent_MCPversion','initialized':True}); save_config('models.json',{'models':[]}); save_config('mcp.json',{'servers':[]}); return redirect('/login')
        return render_template('setup.html')
    @app.route('/login',methods=['GET','POST'])
    def login():
        if request.method=='POST':
            u=authenticate(request.form.get('username',''),request.form.get('password',''))
            if u: session['user']=public_user(u); return redirect('/chat')
            return render_template('login.html',error='账号或密码错误')
        return render_template('login.html')
    @app.post('/logout')
    def logout(): session.clear(); return redirect('/login')
    @app.get('/chat')
    def chat_page(): return render_template('chat.html',user=session['user']) if uid() else redirect('/login')
    @app.get('/api/models')
    def api_models(): return jsonify(models=[{k:m.get(k) for k in ('id','name','default')} for m in models()])
    @app.get('/api/skills')
    def api_skills(): return jsonify(skills=load_skills())
    @app.get('/api/mcp/tools')
    def api_tools(): return jsonify(tools=[{k:t.get(k) for k in ('name','description','inputSchema')} for t in list_tools()])
    @app.get('/api/sessions')
    def api_sessions(): return jsonify(sessions=list_owned(uid()))
    @app.post('/api/sessions')
    def api_session_new(): return jsonify(session=create(uid(),(request.get_json(silent=True) or {}).get('title','新会话')))
    @app.get('/api/sessions/<sid>')
    def api_session_get(sid):
        s=owned(uid(),sid)
        return (jsonify(session=s,messages=get_messages(sid)) if s else (jsonify(error='会话不存在'),404))
    @app.delete('/api/sessions/<sid>')
    def api_session_delete(sid): return (jsonify(success=delete(uid(),sid)) if owned(uid(),sid) else (jsonify(error='会话不存在'),404))
    @app.post('/api/chat')
    def api_chat():
        if not uid(): return jsonify(error='未登录'),401
        x=request.get_json(silent=True) or {}; text=(x.get('message') or '').strip(); s=owned(uid(),x.get('session_id')) if x.get('session_id') else create(uid())
        if not s:return jsonify(error='会话不存在'),404
        if not text:return jsonify(error='消息不能为空'),400
        add_message(s['id'],'user',text)
        try:
            answer=run(x.get('model_id'),[{'role':m['role'],'content':m['content']} for m in get_messages(s['id'])]); add_message(s['id'],answer.get('role','assistant'),answer.get('content',''),{'tool_calls':answer.get('tool_calls',[])})
            return jsonify(session_id=s['id'],message=answer)
        except Exception as e:return jsonify(error=str(e),session_id=s['id']),502
    @app.get('/AdminX')
    def admin_page(): return render_template('admin.html',user=session['user']) if admin() else ('无权限',403)
    @app.get('/AdminX/api/users')
    def admin_users(): return jsonify(users=[public_user(u) for u in users()]) if admin() else (jsonify(error='无权限'),403)
    @app.post('/AdminX/api/users')
    def admin_create():
        if not admin():return jsonify(error='无权限'),403
        x=request.get_json(silent=True) or {}
        try:return jsonify(user=public_user(create_user(x.get('username'),x.get('password'),'user')))
        except ValueError as e:return jsonify(error=str(e)),400
    @app.post('/AdminX/api/users/<user_id>/toggle')
    def admin_toggle(user_id):
        if not admin():return jsonify(error='无权限'),403
        try:return jsonify(user=set_enabled(user_id,not next(u for u in users() if u.get('id')==str(user_id)).get('enabled',True)))
        except (ValueError,StopIteration) as e:return jsonify(error=str(e)),404
    @app.get('/AdminX/api/config')
    def admin_config():
        if not admin():return jsonify(error='无权限'),403
        ms=[dict(m,api_key='********' if m.get('api_key') else '') for m in models()]; ss=[]
        for s in config('mcp.json',{'servers':[]}).get('servers',[]): ss.append(dict(s,headers='********' if s.get('headers') else {}))
        return jsonify(models=ms,mcp=ss)
    return app
