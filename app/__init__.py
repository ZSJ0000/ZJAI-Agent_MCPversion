import os, json
from flask import Flask, redirect, session, request, jsonify, render_template, Response, stream_with_context
from .config import SECRET_KEY
from .services.json_store import ensure_dirs, save_config, config, data, save_data
from .services.auth_service import initialized, authenticate, create_user, users, public_user, set_enabled
from .services.model_service import models, run, stream_reply, agent_stream
from .services.skill_service import load_skills
from .services.mcp_service import list_tools, diagnostics, test_connection, availability_warning, user_connection_warning
from .services.session_service import create, owned, list_owned, get_messages, add_message, delete

def _admin_required(session):
    return session.get('user',{}).get('role') == 'admin'

def _mask_secret(value):
    value=str(value or '')
    if not value: return ''
    if len(value) <= 10: return '*' * len(value)
    return value[:7] + '*' * max(5, len(value)-11) + value[-4:]

def _keep_or_replace(value, original):
    value=str(value or '').strip()
    return original if not value or '*' in value else value

def _safe_models():
    result=[]
    for m in models():
        item=dict(m); item.pop('api_key',None)
        item['api_key_masked']=_mask_secret(m.get('api_key'))
        item['api_key_configured']=bool(m.get('api_key'))
        result.append(item)
    return result

def _safe_mcp():
    result=[]
    for s in config('mcp.json',{'servers':[]}).get('servers',[]):
        item=dict(s); headers=s.get('headers') or {}
        token=headers.get('Authorization','')
        if token.lower().startswith('bearer '): token=token[7:]
        item['token_masked']=_mask_secret(token)
        item['headers_configured']=bool(headers); item['oauth_enabled']=bool(s.get('oauth_enabled', bool(headers.get('Authorization')))); item.pop('headers',None); result.append(item)
    return result

def create_app():
    app=Flask(__name__,template_folder='../templates',static_folder='../static'); app.secret_key=SECRET_KEY; ensure_dirs()
    @app.before_request
    def gate():
        if not initialized() and not (request.path == '/login' or request.path == '/setup' or request.path.startswith('/setup/')) and not request.path.startswith('/static'): return redirect('/setup')
    def uid(): return session.get('user',{}).get('id')
    def admin(): return session.get('user',{}).get('role')=='admin'
    @app.route('/')
    def index(): return redirect('/chat' if uid() else '/login')
    @app.route('/setup', defaults={'step': 1}, methods=['GET', 'POST'])
    @app.route('/setup/<int:step>', methods=['GET', 'POST'])
    def setup(step):
        if initialized(): return render_template('message.html', title='系统已初始化', message='请返回登录页面。')
        if step not in (1, 2, 3, 4): return redirect('/setup/1')
        values=dict(session.get('setup_values', {})); error=None
        if request.method=='POST':
            if step==2:
                u=request.form.get('username','').strip(); p=request.form.get('password',''); c=request.form.get('confirm','')
                if not u or len(p)<6 or p!=c: error='账号不能为空，密码至少 6 位且两次必须一致'
                else: values.update({'username':u,'password':p}); session['setup_values']=values
            elif step==3:
                names=request.form.getlist('api_name[]'); urls=request.form.getlist('api_base_url[]'); keys=request.form.getlist('api_key[]'); mids=request.form.getlist('api_model[]')
                apis=[{'name':names[i] if i<len(names) else 'API '+str(i+1),'url':urls[i].strip(),'key':keys[i] if i<len(keys) else '','model':mids[i] if i<len(mids) else ''} for i in range(len(urls)) if urls[i].strip()]
                if not apis: error='请至少配置一个 API'
                else: values['apis']=apis; session['setup_values']=values
            elif step==4:
                values.update({'site_name':request.form.get('site_name','ZJAI-Agent_MCPversion'),'theme':request.form.get('theme','terminal'),'welcome_message':request.form.get('welcome_message','欢迎使用。'),'mcp_url':request.form.get('mcp_url','').strip(),'mcp_protocol':request.form.get('mcp_protocol','streamable_http'),'mcp_token':request.form.get('mcp_token','').strip()}); session['setup_values']=values
                try:
                    create_user(values['username'],values['password'],'admin')
                    save_config('models.json',{'models':[{'id':'api-'+str(i+1),'name':x['name'],'base_url':x['url'],'api_key':x['key'],'model':x['model'],'enabled':True,'default':i==0} for i,x in enumerate(values.get('apis',[]))]})
                    headers={}
                    if values.get('mcp_token'): headers['Authorization']='Bearer '+values['mcp_token']
                    save_config('mcp.json',{'servers':[{'id':'default','name':'默认 MCP','protocol':values['mcp_protocol'],'url':values['mcp_url'],'enabled':bool(values['mcp_url']),'verify_ssl':True,'headers':headers}]})
                    save_config('app.json',{'name':values['site_name'],'theme':values['theme'],'welcome_message':values['welcome_message'],'initialized':True}); from .services.json_store import write; from .config import DATA_DIR; write(os.path.join(DATA_DIR,'install.lock'),{'initialized':True}); session.pop('setup_values',None); return redirect('/login')
                except Exception as e: error=str(e)
            if error: return render_template('setup_step.html',step=step,values=values,error=error)
            return redirect('/setup/'+str(step+1)) if step<4 else redirect('/login')
        return render_template('setup_step.html',step=step,values=values,error=error)
    @app.route('/login',methods=['GET','POST'])
    def login():
        if request.method=='POST':
            u=authenticate(request.form.get('username',''),request.form.get('password',''))
            if u: session['user']=public_user(u); return redirect('/chat')
            return render_template('login.html',error='账号或密码错误')
        return render_template('login.html')
    @app.post('/logout')
    def logout(): session.clear(); return redirect('/login')
    @app.get('/settings')
    def settings_page(): return render_template('settings.html',user=session['user']) if uid() else redirect('/login')
    @app.get('/settings/<section>')
    def settings_section(section):
        if not uid(): return redirect('/login')
        allowed=('models','mcp','appearance','password','skills')
        if section not in allowed: return redirect('/settings')
        if section in ('models','mcp','appearance','skills') and not admin(): return ('无权限',403)
        return render_template('settings_section.html', user=session['user'], section=section)
    @app.get('/chat')
    def chat_page(): return render_template('chat.html',user=session['user']) if uid() else redirect('/login')
    @app.get('/api/models')
    def api_models(): return jsonify(models=[{k:m.get(k) for k in ('id','name','default')} for m in models()])
    @app.get('/api/skills')
    def api_skills(): return jsonify(skills=load_skills())
    @app.get('/api/mcp/tools')
    def api_tools():
        try:
            return jsonify(tools=[{k:t.get(k) for k in ('name','description','inputSchema')} for t in list_tools()])
        except Exception as exc:
            return jsonify(tools=[], warning='当前无法连接MCP服务器，错误代码：%s。如果不需要MCP，请暂时禁用该服务器。' % str(exc)), 200
    @app.post('/AdminX/api/mcp/test')
    def api_mcp_test():
        if not admin(): return jsonify(error='无权限'),403
        x=request.get_json(silent=True) or {}
        result=test_connection(x.get('id'))
        return jsonify(result), (200 if result.get('ok') else 502)
    @app.get('/api/mcp/diagnostics')
    def api_mcp_diagnostics():
        try:
            return jsonify(diagnostics())
        except Exception as exc:
            return jsonify(tool_count=0, tools=[], errors=['诊断失败: '+str(exc)]), 502
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
    @app.put('/api/sessions/<sid>')
    def api_session_rename(sid):
        current=owned(uid(),sid)
        if not current:return jsonify(error='会话不存在'),404
        x=request.get_json(silent=True) or {}; title=str(x.get('title') or '').strip()
        if not title:return jsonify(error='标题不能为空'),400
        sessions=data('sessions.json',[])
        for item in sessions:
            if item.get('id')==sid: item['title']=title[:100]; current=item; break
        save_data('sessions.json',sessions); return jsonify(session=current)
    def _tool_event_text(kind,name,value):
        if kind=='start':
            return '正在调用 MCP 工具：'+str(name)+'\n参数：'+json.dumps(value,ensure_ascii=False)
        return 'MCP 工具返回：'+str(name)+'\n结果：'+json.dumps(value,ensure_ascii=False)

    @app.post('/api/chat/stream')
    def api_chat_stream():
        if not uid(): return jsonify(error='未登录'), 401
        x=request.get_json(silent=True) or {}
        text=(x.get('message') or '').strip()
        attachments=x.get('attachments') or []
        current=owned(uid(), x.get('session_id')) if x.get('session_id') else create(uid())
        if not current: return jsonify(error='会话不存在'), 404
        if not text: return jsonify(error='消息不能为空'), 400
        add_message(current['id'], 'user', text)
        mcp_warning=''
        history=[{'role':m['role'],'content':m['content']} for m in get_messages(current['id'])]
        if attachments:
            parts=[{'type':'text','text':text}]
            for a in attachments:
                if isinstance(a,dict) and a.get('data','').startswith('data:image/'):
                    parts.append({'type':'image_url','image_url':{'url':a.get('data')}})
                elif isinstance(a,dict):
                    parts.append({'type':'text','text':'附件：%s，类型：%s，大小：%s 字节' % (a.get('name','未知'),a.get('type','未知'),a.get('size',0))})
            history[-1]['content']=parts
        try:
            tool_mode=bool(list_tools())
            if not tool_mode: mcp_warning=user_connection_warning()
        except Exception as exc:
            tool_mode=False
            mcp_warning='当前无法连接MCP服务器，错误代码：%s。如果不需要MCP，请暂时禁用该服务器。' % str(exc)
        if tool_mode:
            chunks=None
        else:
            try:
                chunks=stream_reply(x.get('model_id'), history)
            except Exception as exc: return jsonify(error=str(exc), session_id=current['id']), 502
        def generate():
            parts=[]; tool_events=[]
            try:
                if mcp_warning:
                    parts.append(mcp_warning + '\n\n')
                    yield 'data: '+json.dumps({'type':'delta','content':mcp_warning+'\n\n'}, ensure_ascii=False)+'\n\n'
                if tool_mode:
                    for event in agent_stream(x.get('model_id'), history):
                        if event['type'] == 'plan':
                            yield 'data: '+json.dumps({'type':'tool_start','name':'Plan','data':{'content':event['content']}}, ensure_ascii=False)+'\n\n'
                            yield 'data: '+json.dumps({'type':'tool_result','name':'Plan','data':{'content':event['content'],'isError':False}}, ensure_ascii=False)+'\n\n'
                        elif event['type'] in ('tool_start','tool_result'):
                            tool_events.append(event)
                            yield 'data: '+json.dumps(event, ensure_ascii=False)+'\n\n'
                        elif event['type'] == 'progress' and event.get('content'):
                            yield 'data: '+json.dumps({'type':'progress','content':event['content']}, ensure_ascii=False)+'\n\n'
                        elif event['type'] == 'final' and event.get('content'):
                            parts.append(event['content'])
                            yield 'data: '+json.dumps({'type':'delta','content':event['content']}, ensure_ascii=False)+'\n\n'
                else:
                    for chunk in chunks:
                        parts.append(chunk)
                        yield 'data: '+json.dumps({'type':'delta','content':chunk}, ensure_ascii=False)+'\n\n'
                meta={'tool_events':tool_events} if tool_mode else {}
                add_message(current['id'],'assistant',''.join(parts),meta)
                yield 'data: '+json.dumps({'type':'done','session_id':current['id']}, ensure_ascii=False)+'\n\n'
            except Exception as exc:
                yield 'data: '+json.dumps({'type':'error','error':str(exc)}, ensure_ascii=False)+'\n\n'
        return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no','Connection':'keep-alive'})

    @app.post('/api/chat')
    def api_chat():
        if not uid(): return jsonify(error='未登录'),401
        x=request.get_json(silent=True) or {}; text=(x.get('message') or '').strip(); s=owned(uid(),x.get('session_id')) if x.get('session_id') else create(uid())
        if not s:return jsonify(error='会话不存在'),404
        if not text:return jsonify(error='消息不能为空'),400
        add_message(s['id'],'user',text)
        try:
            warning=user_connection_warning()
            answer=run(x.get('model_id'),[{'role':m['role'],'content':m['content']} for m in get_messages(s['id'])])
            if warning:
                answer=dict(answer); answer['content']=(warning+'\n\n'+(answer.get('content','') or '')).strip()
            add_message(s['id'],answer.get('role','assistant'),answer.get('content',''),{'tool_calls':answer.get('tool_calls',[])})
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
    @app.post('/api/account/password')
    def account_password():
        if not uid(): return jsonify(error='未登录'),401
        from .services.auth_service import authenticate
        x=request.get_json(silent=True) or {}; current=x.get('current_password',''); new=x.get('new_password','')
        u=authenticate(session['user'].get('username'),current)
        if not u:return jsonify(error='当前密码错误'),400
        if not new or len(new)<6:return jsonify(error='新密码至少 6 位'),400
        from werkzeug.security import generate_password_hash
        items=users()
        for item in items:
            if item.get('id')==str(uid()): item['password_hash']=generate_password_hash(new)
        save_data('users.json',items); return jsonify(success=True)

    @app.get('/AdminX/api/settings')
    def admin_settings():
        if not admin():return jsonify(error='无权限'),403
        return jsonify(app=config('app.json',{}),models=_safe_models(),mcp=_safe_mcp(),skills=load_skills())

    @app.put('/AdminX/api/models')
    def admin_models_save():
        if not admin():return jsonify(error='无权限'),403
        payload=request.get_json(silent=True) or {}; incoming=payload.get('models',[])
        if not isinstance(incoming,list):return jsonify(error='models 必须是数组'),400
        clean=[]
        for i,m in enumerate(incoming):
            if not isinstance(m,dict) or not m.get('base_url') or not m.get('model'):continue
            clean.append({'id':str(m.get('id') or 'api-'+str(i+1)),'name':str(m.get('name') or 'API '+str(i+1)),'base_url':str(m['base_url']).strip(),'api_key':_keep_or_replace(m.get('api_key'), next((old.get('api_key','') for old in models() if old.get('id')==m.get('id')), '')),'model':str(m['model']).strip(),'enabled':bool(m.get('enabled',True)),'default':bool(m.get('default',i==0))})
        if clean and not any(m['default'] for m in clean):clean[0]['default']=True
        save_config('models.json',{'models':clean});return jsonify(success=True,models=_safe_models())

    @app.put('/AdminX/api/mcp')
    def admin_mcp_save():
        if not admin():return jsonify(error='无权限'),403
        payload=request.get_json(silent=True) or {}; incoming=payload.get('servers',[])
        if not isinstance(incoming,list):return jsonify(error='servers 必须是数组'),400
        clean=[]
        for i,item in enumerate(incoming):
            if not isinstance(item,dict) or not item.get('url'):continue
            protocol=item.get('protocol','streamable_http')
            if protocol not in ('streamable_http','sse'):return jsonify(error='MCP 协议无效'),400
            incoming_headers=item.get('headers') if isinstance(item.get('headers'),dict) else {}; incoming_token=incoming_headers.get('Authorization',''); old_item=next((old for old in config('mcp.json',{'servers':[]}).get('servers',[]) if old.get('id')==item.get('id')), {}); old_headers=old_item.get('headers',{}) or {}; old_token=old_headers.get('Authorization',''); headers={'Authorization':_keep_or_replace(incoming_token,old_token)} if (incoming_token or old_token) else {}; oauth_enabled=bool(item.get('oauth_enabled', bool(headers.get('Authorization'))))
            clean.append({'id':str(item.get('id') or 'mcp-'+str(i+1)),'name':str(item.get('name') or 'MCP '+str(i+1)),'protocol':protocol,'url':str(item['url']).strip(),'enabled':bool(item.get('enabled',True)),'oauth_enabled':oauth_enabled,'verify_ssl':bool(item.get('verify_ssl',True)),'headers':headers})
        save_config('mcp.json',{'servers':clean});return jsonify(success=True,mcp=_safe_mcp())

    @app.put('/AdminX/api/app-settings')
    def admin_app_save():
        if not admin():return jsonify(error='无权限'),403
        x=request.get_json(silent=True) or {}; value={'name':str(x.get('name') or 'ZJAI-Agent_MCPversion'),'theme':str(x.get('theme') or 'terminal'),'welcome_message':str(x.get('welcome_message') or '欢迎使用。'),'initialized':True}
        save_config('app.json',value);return jsonify(success=True,app=value)

    return app
