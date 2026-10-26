import asyncio
import io
import json
import os
from contextlib import asynccontextmanager

import httpx
import pytest
from starlette.testclient import TestClient

os.environ.setdefault('SECRET_KEY', 'integration-tests-secret-at-least-thirty-two-bytes')
from app import create_app, get_db, seed_data
from integrations.registry import ToolRegistry, ToolError, BoundTools, calculate, _schema
from integrations.mcp_server import create_server
from knowledge.answers import RemoteAnswerer, SUPPORT_CHECK_SYSTEM, DIRECT_FALLBACK_SYSTEM
from knowledge.adapters import Unavailable
from test_knowledge import Embedding, Vectors


@pytest.fixture
def app(tmp_path):
    instance = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'app.db'),
        'STORAGE_PATH': str(tmp_path / 'materials'), 'KNOWLEDGE_EMBEDDING': Embedding(),
        'KNOWLEDGE_VECTORS': Vectors(), 'TOOLS_CONFIG_PATH': ''})
    with instance.app_context(): seed_data()
    return instance


def login(client, username='a_teacher'):
    result = client.post('/api/auth/login', json={'username': username,
        'password': username.replace('_', '-') + '-password'})
    assert result.status_code == 200
    token = result.json['access_token']
    client.environ_base['HTTP_AUTHORIZATION'] = 'Bearer ' + token
    return token


def test_tool_permissions_are_teacher_managed_and_class_scoped(app):
    with app.test_client() as c:
        assert c.get('/api/integrations').status_code == 401
        login(c)
        catalog = c.get('/api/integrations').json
        assert catalog['tools'][0]['id'] == 'builtin:calculator'
        assert catalog['tools'][0]['enabled'] is False
        assert c.put('/api/integrations', json={'enabled_tools': ['builtin:calculator'], 'class_id': 2}).status_code == 400
        assert c.put('/api/integrations', json={'enabled_tools': ['unknown']}).status_code == 400
        assert c.put('/api/integrations', json={'enabled_tools': ['builtin:calculator']}).status_code == 200
        tested = c.post('/api/integrations/tools/builtin:calculator/call', json={'arguments': {'expression': '2*(3+4)'}})
        assert tested.status_code == 200 and json.loads(tested.json['result']) == {'result': 14}
        login(c, 'b_teacher')
        assert c.get('/api/integrations').json['enabled_tools'] == []
        assert c.post('/api/integrations/tools/builtin:calculator/call', json={'arguments': {}}).status_code == 403
        login(c, 'a_student')
        assert c.get('/api/integrations').status_code == 403
        assert c.put('/api/integrations', json={'enabled_tools': []}).status_code == 403
        assert c.post('/api/integrations/tools/builtin:calculator/call', json={'arguments': {}}).status_code == 403


def test_disabled_tools_and_mcp_features_do_not_expose_routes(app):
    app.config['TOOLS_ENABLED'] = False
    with app.test_client() as c:
        login(c)
        assert c.get('/api/integrations').status_code == 404
        assert 'data-nav="integrations"' not in c.get('/materials').text
    _, asgi = create_server(enabled=False)
    with TestClient(asgi, base_url='http://localhost') as c:
        assert c.post('/mcp', json={}).status_code == 404
        assert c.get('/health').status_code == 200


@pytest.mark.parametrize('expression', ['__import__("os").system("whoami")', '2**10000000', 'True+1',
    '1/0', '(1+2j)', '9e999', 'open("secret")', '[1]*1000000000'])
def test_calculator_never_executes_python_or_unbounded_math(expression):
    with pytest.raises(ToolError): calculate(expression)


def test_tool_schema_rejects_network_references():
    with pytest.raises(ToolError): _schema({'type': 'object', '$ref': 'http://127.0.0.1/private'})
    with pytest.raises(ToolError): _schema({'type': 'object', '$dynamicRef': 'https://example.com/schema'})


def test_permission_is_rechecked_and_schema_validated_before_execution():
    registry = ToolRegistry({})
    permitted = True
    bound = BoundTools(registry, {'builtin:calculator'}, lambda _: permitted)
    name = bound.definitions()[0]['function']['name']
    assert json.loads(bound.call(name, {'expression': '6*7'}))['result'] == 42
    with pytest.raises(ToolError): bound.call(name, {'expression': '1', 'extra': 4})
    permitted = False
    with pytest.raises(ToolError): bound.call(name, {'expression': '1'})
    with pytest.raises(ToolError): bound.call('unregistered', {})


def test_http_adapter_keeps_secrets_server_side_and_does_not_follow_redirects(tmp_path, monkeypatch):
    config = tmp_path / 'tools.json'
    config.write_text(json.dumps({'http_tools': [{
        'id': 'search', 'name': '搜索', 'url': 'https://approved.example/search', 'read_only': True,
        'headers_env': {'Authorization': 'TEST_EXTERNAL_AUTH'},
        'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                         'required': ['query'], 'additionalProperties': False}}]}))
    monkeypatch.setenv('TEST_EXTERNAL_AUTH', 'Bearer private-test-key')
    registry = ToolRegistry({'TOOLS_CONFIG_PATH': str(config)})
    public = registry.public_catalog(set())
    assert 'private-test-key' not in json.dumps(public) and 'approved.example' not in json.dumps(public)
    tool = registry.catalog()[0][1]
    original = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer private-test-key'
        return httpx.Response(302, headers={'Location': 'http://private.invalid'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(ToolError): registry.execute(tool, {'query': 'example'})
    assert len(requests) == 1


def test_mcp_client_discovers_allowlisted_readonly_tools_and_calls_them(tmp_path, monkeypatch):
    from mcp.server import MCPServer
    from mcp.client import Client
    from mcp.types import ToolAnnotations
    from integrations import registry as module
    remote = MCPServer('test')
    @remote.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def add(a: int, b: int) -> dict:
        return {'sum': a+b}
    @remote.tool(annotations=ToolAnnotations(read_only_hint=False))
    async def erase() -> str:
        return 'must not be called'
    @asynccontextmanager
    async def connect(config, timeout):
        async with Client(remote) as client: yield client
    monkeypatch.setattr(module, 'mcp_client', connect)
    path = tmp_path / 'tools.json'
    path.write_text(json.dumps({'mcp_servers': [{'id': 'mock', 'name': 'MCP 示例',
        'transport': 'streamable-http', 'url': 'https://approved.example/mcp', 'allowed_tools': ['add', 'erase']}]}))
    registry = ToolRegistry({'TOOLS_CONFIG_PATH': str(path)})
    tools, errors = registry.catalog()
    assert not errors and [tool['id'] for tool in tools] == ['builtin:calculator', 'mcp:mock:add']
    result = json.loads(registry.execute(tools[1], {'a': 4, 'b': 5}))
    assert result['structured'] == {'sum': 9} or any(json.loads(text) == {'sum': 9} for text in result['content'])


def test_model_tool_loop_is_bounded_and_support_check_cannot_call_tools(monkeypatch):
    from knowledge import answers
    registry = ToolRegistry({})
    bound = BoundTools(registry, {'builtin:calculator'}, lambda _: True)
    gateway = RemoteAnswerer({'QA_API_BASE_URL': 'https://gateway.invalid/v1', 'QA_API_KEY': 'test',
        'QA_API_MODEL': 'test', 'QA_API_TIMEOUT': 5})
    gateway.tool_provider = lambda: bound
    requests = []
    def respond(request, **kwargs):
        payload = json.loads(request.data)
        requests.append(payload)
        if len(requests) == 1:
            name = payload['tools'][0]['function']['name']
            message = {'content': None, 'tool_calls': [{'id': 'one', 'type': 'function',
                'function': {'name': name, 'arguments': '{"expression":"6*7"}'}}]}
        else:
            message = {'content': '{"answer":"计算器结果为 42"}'}
        return io.BytesIO(json.dumps({'choices': [{'message': message}]}).encode())
    monkeypatch.setattr(answers.urllib.request, 'urlopen', respond)
    assert gateway.run_direct_fallback('6*7') == '计算器结果为 42'
    assert requests[1]['messages'][-1]['role'] == 'tool'
    assert json.loads(requests[1]['messages'][-1]['content']) == {'result': 42}
    requests.clear()
    def check(request, **kwargs):
        assert 'tools' not in json.loads(request.data)
        return io.BytesIO(b'{"choices":[{"message":{"content":"{\\"supported\\":true}"}}]}')
    monkeypatch.setattr(answers.urllib.request, 'urlopen', check)
    assert gateway._chat(SUPPORT_CHECK_SYSTEM, {}) == {'supported': True}
    bound = BoundTools(registry, {'builtin:calculator'}, lambda _: True)
    name = bound.definitions()[0]['function']['name']
    def loop(request, **kwargs):
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': None, 'tool_calls': [{
            'id': 'loop', 'type': 'function', 'function': {'name': name, 'arguments': '{"expression":"1"}'}}]}}]}).encode())
    monkeypatch.setattr(answers.urllib.request, 'urlopen', loop)
    with pytest.raises(Unavailable, match='4 次'): gateway.run_direct_fallback('repeat')
    assert bound.calls == 4


def _mcp_client(app):
    def backend(request):
        with app.test_client() as client:
            response = client.open(request.url.raw_path.decode(), method=request.method,
                headers={'Authorization': request.headers.get('authorization', '')},
                data=request.content, content_type='application/json')
            return httpx.Response(response.status_code, content=response.data,
                                  headers={'Content-Type': 'application/json'})
    return create_server('http://backend', backend_transport=httpx.MockTransport(backend))[1]


def test_mcp_wire_auth_tools_and_class_isolation(app):
    with app.test_client() as c:
        teacher = login(c)
        assert c.post('/api/materials', data={'title': 'A班资料',
            'file': (io.BytesIO('植物通过光合作用制造养分。'.encode()), 'lesson.txt')}).status_code == 201
        student = login(c, 'a_student')
        other = login(c, 'b_student')
    asgi = _mcp_client(app)
    headers = {'Accept': 'application/json, text/event-stream', 'Authorization': 'Bearer '+student,
               'MCP-Protocol-Version': '2025-11-25'}
    with TestClient(asgi, base_url='http://localhost') as client:
        assert client.post('/mcp', json={}).status_code == 401
        init = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'protocolVersion': '2025-11-25', 'capabilities': {},
                       'clientInfo': {'name': 'test-client', 'version': '1'}}})
        assert init.status_code == 200, init.text
        def rpc(method, params=None, auth=headers):
            return client.post('/mcp', headers=auth, json={'jsonrpc': '2.0', 'id': 2,
                               'method': method, 'params': params or {}})
        listing = rpc('tools/list').json()['result']['tools']
        assert {tool['name'] for tool in listing} == {'get_classroom_profile', 'list_class_materials',
            'search_class_knowledge', 'read_class_passage'}
        a = rpc('tools/call', {'name': 'list_class_materials', 'arguments': {}}).json()['result']
        assert 'A班资料' in json.dumps(a, ensure_ascii=False) and 'storage_path' not in json.dumps(a)
        b = rpc('tools/call', {'name': 'list_class_materials', 'arguments': {}},
                {**headers, 'Authorization': 'Bearer '+other}).json()['result']
        assert 'A班资料' not in json.dumps(b, ensure_ascii=False)
        searched = rpc('tools/call', {'name': 'search_class_knowledge',
            'arguments': {'query': '植物', 'mode': 'keyword'}}).json()['result']
        assert '光合作用' in json.dumps(searched, ensure_ascii=False)
        records = json.loads(searched['content'][0]['text'])['results']
        foreign = rpc('tools/call', {'name': 'read_class_passage',
            'arguments': {'chunk_id': records[0]['chunk_id']}},
            {**headers, 'Authorization': 'Bearer '+other}).json()['result']
        assert foreign.get('isError') is True
        assert '光合作用' not in json.dumps(foreign, ensure_ascii=False)
        malicious = rpc('tools/call', {'name': 'read_class_passage',
            'arguments': {'chunk_id': '../../api/tutoring/config'}}).json()['result']
        assert malicious.get('isError') is True
        # Host/Origin validation is still enforced behind Nginx.
        assert rpc('tools/list', auth={**headers, 'Host': 'evil.example'}).status_code == 421
        with app.test_client() as c:
            c.post('/api/auth/logout', headers={'Authorization': 'Bearer '+student})
        assert rpc('tools/list').status_code == 401


def test_mcp_stdio_transport_runs_a_real_sdk_subprocess(tmp_path):
    import sys
    path = tmp_path / 'sample_mcp.py'
    path.write_text("""from mcp.server import MCPServer
from mcp.types import ToolAnnotations
server = MCPServer('stdio-test')
@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def multiply(a: int, b: int) -> dict:
    return {'product': a*b}
if __name__ == '__main__':
    server.run()
""")
    config = tmp_path / 'tools.json'
    config.write_text(json.dumps({'mcp_servers': [{'id': 'local', 'name': '本地测试',
        'transport': 'stdio', 'command': sys.executable, 'args': [str(path)],
        'allowed_tools': ['multiply']}]}))
    registry = ToolRegistry({'TOOLS_CONFIG_PATH': str(config)})
    tools, errors = registry.catalog()
    assert not errors and len(tools) == 2
    output = json.loads(registry.execute(tools[1], {'a': 3, 'b': 7}))
    assert json.loads(output['content'][0]) == {'product': 21}


def test_mcp_http_transport_and_modern_sdk_client(app, monkeypatch):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client
    from integrations.registry import mcp_client
    with app.test_client() as c:
        token = login(c, 'a_student')
    asgi = _mcp_client(app)
    original = httpx2.AsyncClient
    monkeypatch.setenv('TEST_MCP_AUTH', 'Bearer '+token)
    def client_factory(**kwargs):
        return original(transport=httpx2.ASGITransport(app=asgi), **kwargs)
    monkeypatch.setattr(httpx2, 'AsyncClient', client_factory)
    async def run():
        async with asgi.router.lifespan_context(asgi):
            async with mcp_client({'transport': 'streamable-http', 'url': 'http://localhost/mcp',
                                  'headers_env': {'Authorization': 'TEST_MCP_AUTH'}}, 5) as client:
                listing = await client.list_tools()
                assert len(listing.tools) == 4
                result = await client.call_tool('get_classroom_profile')
                assert not result.is_error
                assert 'a_student' in result.content[0].text
    asyncio.run(run())


def test_student_ask_uses_only_tools_enabled_for_their_own_class(app, monkeypatch):
    from knowledge import answers
    gateway = RemoteAnswerer({'QA_API_BASE_URL': 'https://gateway.invalid/v1', 'QA_API_KEY': 'test',
                              'QA_API_MODEL': 'test'})
    gateway.tool_provider = app.extensions['answerer'].tool_provider
    app.extensions['answerer'] = gateway
    assert gateway.tool_provider() is None  # Background code has no classroom authority.
    requests = []
    def respond(request, **kwargs):
        payload = json.loads(request.data)
        requests.append(payload)
        if payload.get('tools') and payload['messages'][-1]['role'] != 'tool':
            message = {'content': None, 'tool_calls': [{'id': 'class-call', 'type': 'function',
                'function': {'name': payload['tools'][0]['function']['name'], 'arguments': '{"expression":"6*7"}'}}]}
        else:
            answer = '计算器结果为 42' if payload['messages'][-1]['role'] == 'tool' else '未启用工具'
            message = {'content': json.dumps({'answer': answer})}
        return io.BytesIO(json.dumps({'choices': [{'message': message}]}).encode())
    monkeypatch.setattr(answers.urllib.request, 'urlopen', respond)
    with app.test_client() as c:
        login(c)
        c.put('/api/integrations', json={'enabled_tools': ['builtin:calculator']})
        login(c, 'a_student')
        response = c.post('/api/knowledge/ask', json={'question': '6乘7是多少', 'mode': 'keyword'})
        assert response.status_code == 200 and response.json['answer'] == '计算器结果为 42'
        assert json.loads(requests[-1]['messages'][-1]['content']) == {'result': 42}
        login(c, 'b_student')
        response = c.post('/api/knowledge/ask', json={'question': '6乘7是多少', 'mode': 'keyword'})
        assert response.status_code == 200 and response.json['answer'] == '未启用工具'
        assert 'tools' not in requests[-1]
