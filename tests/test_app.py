import os, tempfile
def test_setup_login_and_sessions():
    os.chdir(tempfile.mkdtemp()); os.environ['ZJAI_SECRET_KEY']='test'
    from app import create_app
    c=create_app().test_client(); assert c.get('/setup').status_code==200
    assert c.post('/setup',data={'username':'admin','password':'password','confirm':'password'}).status_code==302
    assert c.post('/login',data={'username':'admin','password':'password'}).status_code==302
    assert c.get('/api/sessions').status_code==200
