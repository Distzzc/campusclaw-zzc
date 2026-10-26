# 外部工具与双向 MCP

本项目有两个接入方向：课堂 AI 调用外部工具；外部 MCP 客户端读取本项目知识库。两者独立配置，均沿用现有班级隔离。未启用任何工具时，原有问答流程保持不变。

## 课堂 AI 调用工具

1. 管理员按下文配置外部服务；内置计算器无需配置即可出现在目录中。
2. 教师登录，在「工具与 MCP」中选择工具并保存，仅对本班生效。
3. 学生在原有「解题引导」或「知识检索 → 根据材料回答」中提问。支持 function/tool calling 的远程模型会按需选用工具。

需要已配置 `QA_API_BASE_URL`、`QA_API_KEY`、`QA_API_MODEL`，并使用支持 Chat Completions `tools` 的网关。本地生成模型暂不调用外部工具。一次生成最多执行 4 次工具调用；工具返回作为外部数据交给模型，独立于班级 evidence。原有引用校验仍然只允许本班知识库资料。

所有工具默认停用。学生不能管理或手动调试工具。教师配置接口不接受 class_id、URL、命令或密钥；班级从当前登录账号读取。每次执行前重新检查令牌、当前班级和启用状态，停用或注销后不再执行新的调用。

### 注册 HTTP JSON 工具

编辑 `config/integrations.json`，参考同目录的 `integrations.example.json`。配置文件由部署管理员维护，不通过页面执行任意 URL 或命令。

```json
{
  "http_tools": [
    {
      "id": "course-search",
      "name": "外部课程检索",
      "description": "检索学校提供的课程资料。",
      "read_only": true,
      "url": "https://tools.example.edu/search",
      "headers_env": {"Authorization": "COURSE_TOOL_AUTH"},
      "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "maxLength": 1000}},
        "required": ["query"],
        "additionalProperties": false
      }
    }
  ],
  "mcp_servers": []
}
```

工具收到 POST JSON，请求体就是通过 schema 校验的模型参数，响应必须是 JSON。不跟随重定向；响应最多 32 KB；默认超时 10 秒（`TOOLS_TIMEOUT`，上限 30 秒）。目前自动调用仅接受管理员明确声明 `read_only: true` 的服务；管理员应确保服务实际只读。

凭据示例：在服务端 `.env` 中加入 `COURSE_TOOL_AUTH=Bearer your-service-token`。Compose 将 `.env` 注入 app 服务，页面不会收到这些密钥。普通 Python 启动请自行导出环境变量。不要把密钥、内嵌密码 URL 或真实班级数据放进示例文件。

### 连接外部 MCP 服务

支持 Streamable HTTP 和 stdio；使用官方 MCP Python SDK 2.3.0。配置示例：

```json
{
  "http_tools": [],
  "mcp_servers": [
    {
      "id": "school-library",
      "name": "学校图书馆 MCP",
      "transport": "streamable-http",
      "url": "https://library.example.edu/mcp",
      "headers_env": {"Authorization": "LIBRARY_MCP_AUTH"},
      "allowed_tools": ["search_books"]
    }
  ]
}
```

`LIBRARY_MCP_AUTH` 在服务端设置为外部 MCP 服务要求的 Authorization 完整值。此处不复用校园用户的登录令牌。外部服务需支持预配置凭据或匿名访问；本版不提供外部 OAuth 浏览器授权流程。

本地 MCP 程序可使用：

```json
{
  "http_tools": [],
  "mcp_servers": [
    {
      "id": "local-library",
      "name": "本地图书检索",
      "transport": "stdio",
      "command": "python",
      "args": ["/opt/tools/library_server.py"],
      "env_allowlist": ["LIBRARY_API_KEY"],
      "allowed_tools": ["search_books"]
    }
  ]
}
```

stdio 程序必须已安装在 app 容器或当前运行环境中；需要的文件应自行以只读卷挂载。命令以参数数组直接启动，不经过 shell，不从教师提示或模型参数中读取。只有 `allowed_tools` 内且服务声明 `readOnlyHint: true` 的工具会显示和执行；每次连接重查只读声明与参数 schema。仅支持文本/结构化结果，不执行 MCP prompts、sampling 或客户端文件 roots。

每个服务的目录缓存 60 秒，可在教师页点击「刷新连接」。配置文件更改后重新启动 app：`docker compose restart app`。若添加或修改 `.env`，需 `docker compose up -d --force-recreate app` 以更新容器环境。配置文件默认已只读挂载。

### 配置 API

均使用现有 Bearer 令牌，仅教师可访问：

- `GET /api/integrations`：读取安全的工具目录、本班启用状态和 MCP 地址；`?refresh=1` 重新发现外部 MCP 工具。不返回 URL、命令或密钥。
- `PUT /api/integrations`：JSON `{"enabled_tools":["builtin:calculator"]}`；最多 32 项。断线但原已启用的工具可保留或停用。
- `POST /api/integrations/tools/<tool_id>/call`：教师调试本班已启用的工具。JSON `{"arguments":{"expression":"6*7"}}`，结果为 `{"result":"{\"result\":42}"}`。工具 ID 放在 URL 路径中时应编码。

## 外部客户端访问 CampusClaw MCP

Compose 中独立的 `mcp-server` 服务通过 Nginx 暴露 `/mcp`，不直接公开 MCP 容器端口，也不挂载数据库或签名密钥。每个请求和工具调用均经由现有 API 校验登录身份；令牌撤销、过期或用户转班后，访问权限随之变化。

Docker 地址：`http://localhost:5001/mcp`（实际端口由 `APP_PORT` 决定）。通过自己的服务器域名使用时，地址替换为 `https://你的域名/mcp`。

### 认证和连接

1. 使用现有登录接口 `POST /api/auth/login`，JSON 为自己的 `username`、`password`，获取 `access_token`。
2. 在支持自定义 HTTP 请求头的 MCP 客户端中配置 Streamable HTTP 地址和 `Authorization: Bearer <access_token>`。令牌必须放在请求头，不能放在 URL。
3. 令牌有效期沿用原项目（当前为一小时）。过期后重新登录并替换客户端配置；退出登录会撤销对应令牌。

这是现有账号的 Bearer 接入，不实现 OAuth 授权服务器、动态客户端注册或自动续期。只支持 OAuth 登录的外部客户端需要额外适配；不要把服务器 `SECRET_KEY` 当作登录令牌。

可用工具：

| 工具 | 用途 |
| --- | --- |
| `get_classroom_profile` | 当前账号角色及班级 |
| `list_class_materials` | 本班材料列表，隐藏服务器文件路径 |
| `search_class_knowledge` | 本班检索，参数 query、mode、limit |
| `read_class_passage` | 使用检索返回的 chunk_id 读取本班有效原文 |

不对外提供上传、删除、教师配置、其他学生会话或执行外部工具的入口。外部客户端自行决定如何综合工具结果，MCP 返回的原文不代表本项目已核验该客户端生成的答案。

### 官方 SDK 连接示例

先安装 `mcp==2.3.0`，设置 `CAMPUSCLAW_MCP_URL` 和 `CAMPUSCLAW_ACCESS_TOKEN`，再运行：

```python
import asyncio
import os
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with httpx2.AsyncClient(headers={
        "Authorization": "Bearer " + os.environ["CAMPUSCLAW_ACCESS_TOKEN"]
    }) as http:
        transport = streamable_http_client(os.environ["CAMPUSCLAW_MCP_URL"], http_client=http)
        async with Client(transport) as client:
            tools = await client.list_tools()
            print([tool.name for tool in tools.tools])
            result = await client.call_tool("search_class_knowledge", {
                "query": "光合作用", "mode": "keyword", "limit": 5
            })
            print(result)

asyncio.run(main())
```

服务使用无会话的 Streamable HTTP，SDK 自动协商协议，同时支持旧客户端的 initialize/tools/list/tools/call 流程。无须在项目中手写 JSON-RPC。

### 部署和开关

```bash
docker compose up -d --build
docker compose ps
curl http://localhost:5001/health
```

正式域名访问时，在 `.env` 中配置 `MCP_ALLOWED_HOSTS=你的域名` 和 `MCP_ALLOWED_ORIGINS=https://你的域名`；如还需要本地访问，逗号分隔追加 `localhost,localhost:*` 等现有值。局域网访问需增加相应 IP/主机名。Host/Origin 检查默认启用，不使用通配任意来源。

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| TOOLS_ENABLED | true | 显示教师工具页及启用工具能力；班级默认无启用项 |
| TOOLS_CONFIG_PATH | 空（Python）/ `/app/config/integrations.json`（Compose） | 管理员工具配置文件 |
| TOOLS_TIMEOUT | 10 | 单次工具调用及单个 MCP 服务发现超时秒数，上限 30 |
| MCP_SERVER_ENABLED | true | 控制对外 MCP 接口，关闭后 `/mcp` 返回 404 |
| CAMPUSCLAW_API_URL | `http://127.0.0.1:5000` | 独立 MCP 进程访问课堂 API 的内部地址；Compose 自动设置 |
| MCP_ALLOWED_HOSTS | 本机及 MCP 服务名 | MCP Host 白名单，支持 `主机:*` |
| MCP_ALLOWED_ORIGINS | 本机 HTTP 来源 | MCP Origin 白名单 |

本机 Python 部署需同时运行 Flask 和 `python -m integrations.mcp_server`，后者默认监听 8000 端口。只使用课堂内部工具时可以不启动 MCP 服务。关闭功能会保留班级工具配置，重新启用后可恢复。

## 验证

`python -m pytest -q` 覆盖认证、班级隔离、工具参数/结果限制、模型工具调用循环、引用独立校验、MCP 协议和 HTTP/stdio 适配；`npm run test:ui` 覆盖教师工具页面、保存配置、角色入口及原有问答行为。测试使用本地 SDK 和替身服务，不调用付费模型或真实第三方工具。

参考：[官方 MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)、[Streamable HTTP 部署](https://py.sdk.modelcontextprotocol.io/run/)。
