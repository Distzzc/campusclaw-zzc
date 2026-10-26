"""Run twice (write/read) in recreated network-disabled validation containers."""
import io
import sys
sys.path.insert(0, '/app')
from app import app, seed_data

with app.app_context():
    if sys.argv[1] == 'write':
        seed_data()
client = app.test_client()
assert client.get('/health').status_code == 200
response = client.post('/api/auth/login', json={'username':'a_teacher','password':'a-teacher-password'})
assert response.status_code == 200
client.environ_base['HTTP_AUTHORIZATION'] = 'Bearer ' + response.json['access_token']
if sys.argv[1] == 'write':
    response=client.post('/api/materials', data={'title':'持久化验收','file':(
        io.BytesIO('植物通过光合作用制造养分。'.encode()),'lesson.txt')})
    assert response.json['material']['search_index_status']=='indexed', response.json
for mode in ('keyword','vector','hybrid'):
    response=client.post('/api/search',json={'query':'光合作用','mode':mode})
    assert response.status_code==200 and response.json['results'], response.json
    item=response.json['results'][0]
    assert client.get(item['source_url']).json['excerpt']==item['excerpt']
    assert client.get(item['download_url']).status_code==200
app.config['KNOWLEDGE_ENABLED']=False
assert client.post('/api/search',json={'query':'光合作用'}).status_code==404
assert b'data-knowledge-form' not in client.get('/materials').data
assert client.get(item['download_url']).status_code==200
print('container persistence and rollback:',sys.argv[1],'passed',flush=True)
