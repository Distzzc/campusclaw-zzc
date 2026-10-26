"""Bounded, allowlisted HTTP/MCP adapters. Connection details never come from a model."""
import ast
import asyncio
import hashlib
import json
import math
import operator
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, ValidationError


class ToolError(ValueError):
    pass


CALCULATOR = {
    'id': 'builtin:calculator', 'name': '计算器', 'description': '计算数字算式，支持 + - * / % 和 **，不能执行代码。',
    'source': '内置工具', 'kind': 'builtin',
    'schema': {'type': 'object', 'properties': {'expression': {'type': 'string', 'maxLength': 200}},
               'required': ['expression'], 'additionalProperties': False},
}


def calculate(expression):
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
    try:
        tree = ast.parse(expression, mode='eval')
        if len(list(ast.walk(tree))) > 60:
            raise ValueError()
        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                value = node.value
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp) and type(node.op) in ops:
                a, b = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Pow) and abs(b) > 100:
                    raise ValueError()
                value = ops[type(node.op)](a, b)
            else:
                raise ValueError()
            if type(value) not in (int, float) or abs(value) > 1e100 or not math.isfinite(value):
                raise ValueError()
            return value
        return {'result': visit(tree.body)}
    except (ValueError, SyntaxError, ArithmeticError, TypeError, RecursionError):
        raise ToolError('算式无效或超出计算范围') from None


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS class_external_tools (
        class_id INTEGER NOT NULL REFERENCES classes(id), tool_id TEXT NOT NULL,
        PRIMARY KEY (class_id, tool_id))''')
    db.commit()


def enabled_ids(db, class_id):
    return {row[0] for row in db.execute('SELECT tool_id FROM class_external_tools WHERE class_id=?', (class_id,))}


def _schema(schema):
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise ToolError('工具参数必须为 JSON object schema')
    # Do not allow a remote schema to fetch additional schemas from arbitrary URLs.
    def check(value):
        if isinstance(value, dict):
            if '$ref' in value and not str(value['$ref']).startswith('#/'):
                raise ToolError('工具参数不支持外部 schema 引用')
            if '$dynamicRef' in value or '$recursiveRef' in value:
                raise ToolError('工具参数不支持动态 schema 引用')
            for child in value.values(): check(child)
        elif isinstance(value, list):
            for child in value: check(child)
    check(schema)
    Draft202012Validator.check_schema(schema)
    return schema


def _url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ToolError('工具连接地址必须为无内嵌凭据的 HTTP(S) URL')
    return value


def _headers(config):
    result = {}
    for name, variable in config.get('headers_env', {}).items():
        value = os.environ.get(variable)
        if not value:
            raise ToolError('工具所需的服务端凭据尚未配置')
        if name.lower() in ('host', 'cookie', 'content-length') or '\n' in value or '\r' in value:
            raise ToolError('工具请求头配置无效')
        result[name] = value
    return result


@asynccontextmanager
async def mcp_client(config, timeout):
    from mcp.client import Client
    if config['transport'] == 'stdio':
        from mcp.client.stdio import StdioServerParameters
        # Only an administrator-controlled file can specify a program; never use a shell.
        env = {key: os.environ[key] for key in config.get('env_allowlist', []) if key in os.environ}
        params = StdioServerParameters(command=config['command'], args=config.get('args', []), env=env)
        async with Client(params, read_timeout_seconds=timeout) as client:
            yield client
    else:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        async with httpx2.AsyncClient(headers=_headers(config), timeout=timeout, follow_redirects=False,
                                      trust_env=False) as http:
            async with Client(streamable_http_client(config['url'], http_client=http),
                              read_timeout_seconds=timeout) as client:
                yield client


async def _list_tools(client):
    tools, cursor = [], None
    for _ in range(10):
        page = await client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if not cursor: return tools
    raise ToolError('MCP 工具列表过大')


class ToolRegistry:
    def __init__(self, config):
        self.timeout = max(1, min(float(config.get('TOOLS_TIMEOUT', 10)), 30))
        self.max_bytes = 32000
        self.sources = []
        self.http_tools = []
        self._cache = None
        self._cache_until = 0
        path = config.get('TOOLS_CONFIG_PATH', '')
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text())
            if not isinstance(data, dict) or set(data) - {'http_tools', 'mcp_servers'}:
                raise ValueError()
            if len(data.get('http_tools', [])) > 32 or len(data.get('mcp_servers', [])) > 8:
                raise ValueError()
            ids = set()
            for item in data.get('http_tools', []):
                self._identity(item, ids)
                if item.get('read_only') is not True:
                    raise ToolError('自动调用仅支持管理员标记为 read_only 的工具')
                self.http_tools.append(dict(item, url=_url(item['url']), schema=_schema(item['input_schema'])))
            for item in data.get('mcp_servers', []):
                self._identity(item, ids)
                if not isinstance(item.get('allowed_tools'), list) or not all(isinstance(x, str) for x in item['allowed_tools']):
                    raise ToolError('MCP 服务必须显式指定 allowed_tools')
                if item.get('transport') == 'streamable-http':
                    _url(item['url'])
                elif item.get('transport') == 'stdio':
                    if not isinstance(item.get('command'), str) or not item['command']:
                        raise ValueError()
                    if not isinstance(item.get('args', []), list) or not all(isinstance(x, str) for x in item.get('args', [])):
                        raise ValueError()
                else:
                    raise ToolError('MCP 传输仅支持 streamable-http 和 stdio')
                self.sources.append(item)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ToolError('外部工具配置文件无效，请检查服务端配置') from error

    @staticmethod
    def _identity(item, ids):
        if (not isinstance(item, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', item.get('id', ''))
                or item['id'] in ids or not isinstance(item.get('name'), str)):
            raise ValueError('工具标识或名称无效')
        ids.add(item['id'])

    async def _discover(self, config):
        async with asyncio.timeout(self.timeout):
            async with mcp_client(config, self.timeout) as client:
                return await _list_tools(client)

    async def _discover_sources(self):
        return await asyncio.gather(*(self._discover(config) for config in self.sources), return_exceptions=True)

    def catalog(self, refresh=False):
        if not refresh and self._cache is not None and time.monotonic() < self._cache_until:
            return self._cache
        tools, errors = [dict(CALCULATOR)], []
        for item in self.http_tools:
            tools.append({'id': 'http:' + item['id'], 'name': item['name'], 'source': 'HTTP 工具',
                          'kind': 'http', 'description': str(item.get('description', ''))[:1500],
                          'schema': item['schema'], '_config': item})
        discoveries = asyncio.run(self._discover_sources()) if self.sources else []
        for config, discovered in zip(self.sources, discoveries):
            try:
                if isinstance(discovered, BaseException):
                    raise ToolError('MCP 服务不可用')
                for tool in discovered:
                    if tool.name not in config['allowed_tools']:
                        continue
                    # An allowlist alone cannot override a server's side-effect declaration.
                    if not tool.annotations or tool.annotations.read_only_hint is not True:
                        continue
                    tools.append({'id': f'mcp:{config["id"]}:{tool.name}', 'name': tool.title or tool.name,
                        'source': config['name'], 'kind': 'mcp', 'description': (tool.description or '')[:1500],
                        'schema': _schema(tool.input_schema), '_config': config, '_remote_name': tool.name})
            except Exception:
                errors.append({'source': config['id'], 'message': 'MCP 服务暂不可用，或工具定义无效'})
        self._cache, self._cache_until = (tools, errors), time.monotonic() + 60
        return self._cache

    def public_catalog(self, enabled):
        tools, errors = self.catalog()
        return {'tools': [{key: value for key, value in dict(tool, enabled=tool['id'] in enabled).items()
                           if not key.startswith('_')} for tool in tools], 'errors': errors, 'enabled_tools': sorted(enabled)}

    async def _call_mcp(self, tool, arguments):
        async with asyncio.timeout(self.timeout):
            async with mcp_client(tool['_config'], self.timeout) as client:
                # Refresh declarations on each connection; reject newly-mutating tools.
                current = await _list_tools(client)
                declared = next((item for item in current if item.name == tool['_remote_name']), None)
                if not declared or not declared.annotations or declared.annotations.read_only_hint is not True:
                    raise ToolError('MCP 工具已不可用或不再是只读工具')
                Draft202012Validator(_schema(declared.input_schema)).validate(arguments)
                result = await client.call_tool(tool['_remote_name'], arguments)
                if result.is_error:
                    raise ToolError('MCP 工具调用失败')
                return {'structured': result.structured_content,
                        'content': [item.text for item in result.content if item.type == 'text']}

    async def _call_http(self, config, arguments):
        async with asyncio.timeout(self.timeout):
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream('POST', config['url'], json=arguments, headers=_headers(config)) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > self.max_bytes:
                            raise ToolError('工具返回内容过长')
            return json.loads(data)

    def execute(self, tool, arguments):
        try:
            if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 16000:
                raise ToolError('工具参数无效或过长')
            Draft202012Validator(tool['schema']).validate(arguments)
            if tool['kind'] == 'builtin':
                result = calculate(arguments['expression'])
            elif tool['kind'] == 'http':
                result = asyncio.run(self._call_http(tool['_config'], arguments))
            else:
                result = asyncio.run(self._call_mcp(tool, arguments))
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode()) > self.max_bytes:
                raise ToolError('工具返回内容过长')
            return encoded
        except ToolError:
            raise
        except ValidationError:
            raise ToolError('工具参数不符合要求') from None
        except Exception:
            raise ToolError('工具调用失败或超时，请检查服务状态') from None


class BoundTools:
    """A class/user snapshot plus a fresh permission check before every execution."""
    def __init__(self, registry, allowed, authorize):
        self.registry = registry
        tools, _ = registry.catalog() if allowed else ([], [])
        self.tools = {'tool_' + hashlib.sha256(t['id'].encode()).hexdigest()[:20]: t
                      for t in tools if t['id'] in allowed}
        self.authorize = authorize
        self.calls = 0

    def definitions(self):
        if self.calls >= 4:
            return []
        return [{'type': 'function', 'function': {'name': name, 'description': tool['description'],
                 'parameters': tool['schema']}} for name, tool in list(self.tools.items())[:32]]

    def call(self, name, arguments):
        tool = self.tools.get(name)
        if tool is None or not self.authorize(tool['id']):
            raise ToolError('工具未启用或当前账号无权调用')
        if self.calls >= 4:
            raise ToolError('每次回答最多执行 4 次工具调用')
        self.calls += 1
        return self.registry.execute(tool, arguments)
