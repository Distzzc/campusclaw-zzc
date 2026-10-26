# CampusClaw

最小 Flask + SQLite 课堂材料服务，提供教师/学生登录、角色权限、班级隔离和教师材料入库。

## 本地启动

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="use-a-long-random-server-secret"
export DATABASE_PATH="$PWD/data/app.db"
export STORAGE_PATH="$PWD/data/materials"
python app.py
```

开发/测试预置数据只在非生产环境显式加载：

```bash
FLASK_APP=app.py flask seed-data
```

预置账号：`a_teacher`、`a_student`、`b_teacher`、`b_student`。预置密码仅供开发测试，不能用于生产。

## Docker Compose

复制 `.env.example` 并设置服务端 `SECRET_KEY`，然后执行：

```bash
cp .env.example .env
docker compose up --build
```

Nginx 反向代理会对外暴露 `APP_PORT`，Flask/Gunicorn 只在 Compose 内部网络监听。健康检查：

```bash
curl http://localhost:5001/health
```

打开 `http://localhost:5001` 进入正式登录页。登录后会进入课堂工作台：教师可以上传材料，学生以只读方式查看本班材料。页面会自动适配桌面和移动视口，并提供加载、空状态、上传成功和错误反馈。

应用通过 `/api/materials` 提供材料列表和教师上传接口；服务端依据JWT 认证用户在数据库中的当前班级过滤数据，客户端传入的班级标识不参与授权。

生产部署必须通过服务端环境变量注入随机 `SECRET_KEY` 并启用 HTTPS。接口仅接受 JWT Bearer Token；签名密钥不能放入前端、请求参数、表单或请求头，也不能启用预置账号。

## 班级知识库检索与问答

使用 Python 3.12。新增依赖较大，现有 Python 3.9 环境请新建虚拟环境后安装 `requirements.txt`。应用使用 JWT Bearer 认证，从数据库当前用户获取班级范围。支持 UTF-8 TXT、Markdown、文本型 PDF、DOCX（含表格）；不包含扫描件 OCR。文件存储状态与正文索引状态独立，正文索引失败仍能授权下载原文件。

### 显式准备本地模型

应用运行期间不自动联网下载模型，也不将课堂内容发送至外部模型服务。首次准备需要联网和约 3.5 GB 模型磁盘空间；运行内存还需容纳嵌入模型、生成模型及其上下文。准备脚本记录仓库与精确修订，验收结果记录在本地报告中：

```bash
python scripts/prepare_models.py
export EMBEDDING_MODEL_PATH="$PWD/data/models/embedding"
export EMBEDDING_MODEL_REVISION="填写 data/models/manifest.json 中 embedding.revision"
export QA_MODEL_PATH="$PWD/data/models/qa"
python scripts/validate_knowledge_models.py
```

默认准备多语言 MiniLM 嵌入和 Qwen2.5-1.5B-Instruct；可通过脚本参数指定其他兼容本地模型。`data/models` 不提交 Git，也不打入镜像；Compose 只读挂载该目录。模型缺失时索引显示失败，向量/混合检索与需要模型的问答返回 503，登录和原文件下载仍可用。问答采用单轮检索、生成、逐条引用与证据支持检查；资料不足明确拒答。模型仍可能误判证据支持性，应通过引用原文核验。

### 回填、重建和配置

```bash
flask --app app reindex-materials                 # pending/failed
flask --app app reindex-materials --material-id 1 # 指定材料
flask --app app reindex-materials --all           # 全量重建
```

索引只在正文片段、班级 FTS 和向量全部成功后发布。重新处理期间材料暂不可检索；失败或进程中断后重试，活动租约到期前会提示 busy。索引版本包含正文、分块配置和嵌入模型标识；替换模型权重必须更新 `EMBEDDING_MODEL_REVISION` 并执行全量重建。旧版本引用返回 404，不自动指向新文本。

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| VECTOR_PATH | 数据库目录/vector-index | Chroma 持久化目录 |
| VECTOR_MIN_SIMILARITY | 0.35 | 余弦相似度过滤阈值，真实模型验收后校准 |
| INDEX_TIMEOUT | 120 秒 | 索引作业发布截止与租约时间 |
| INDEX_MAX_CHARACTERS | 2000000 | 最大抽取正文字符数 |
| MAX_CONTENT_LENGTH | 25 MiB | 最大请求体 |
| QA_CONTEXT_TOKENS | 4096 | 包含指令和输出预留的上下文预算 |
| QA_MAX_NEW_TOKENS | 768 | 每次生成输出上限 |
| QA_TIMEOUT | 120 秒 | 完整问答推理进程超时，超时终止释放资源 |
| QA_CONCURRENCY | 1 | 同一数据目录的跨进程推理并发数 |
| QA_THREADS | 4 | 本地推理 CPU 线程数 |
| KNOWLEDGE_ENABLED | true | 新入口开关，false 可回滚界面及 API |

镜像安装 CPU 版 PyTorch，避免下载未使用的 CUDA 运行库。Gunicorn 采用一个进程、四个线程，避免复制嵌入模型并适配嵌入式 Chroma；生成在独立可终止进程内运行。Gunicorn 与 Nginx 超时均为 180 秒，大于内部 120 秒。如增加内部超时，需同步调整代理超时。SQLite 与向量目录均在 Compose `app-data` 卷中，重建容器保留索引。部署前备份数据卷；回滚可关闭 `KNOWLEDGE_ENABLED` 或切回旧镜像，保留原文件与数据库，新列不会改变原下载契约。

### API 与验收

- 交互式 Swagger UI：`/api/docs`；OpenAPI 3.0 文档：`/static/openapi.yaml`。页面可以匿名打开；在 Authorize 输入登录接口返回的 `access_token` 后，即可对受保护接口执行 Try it out。Swagger UI 静态资源由固定版本的 unpkg CDN 提供，浏览器需要能访问该 CDN。

- `POST /api/search`：`{"query":"光合作用","mode":"hybrid","limit":5}`；模式支持 keyword/vector/hybrid，默认 hybrid，limit 为 1–20。
- `POST /api/knowledge/ask`：`{"question":"植物如何制造养分？","mode":"hybrid"}`；返回 answered 或 insufficient_evidence、回答及编号引用。
- `GET /api/knowledge/chunks/<chunk_id>`：获取当前版本完整原文与出处。

三种接口均需要 `Authorization: Bearer <token>`。来源下载也通过携带 Bearer 的请求进行，Token 不放入 URL。检索先限定本班候选再融合、核验版本。生成结果无有效引用返回 502，模型/索引依赖失败返回 503，生成期间证据变化返回 409；其他班级、过期或不存在片段统一返回 404。

## Docker API 自动化验收

仓库根目录的 `apitest/` 按接口 URL 和 HTTP 方法组织黑盒用例，例如 `cases/api/auth/login/POST.py` 对应 `POST /api/auth/login`。每个脚本的 `run(ctx)` 不仅核对响应，还通过 `docker exec` 对运行容器中的 SQLite 执行只读查询，比较令牌、提示词、技能、搜索片段、会话、回答和引用等持久化内容。会话/测试技能使用完毕后会通过公开 API 清理；提示词测试会恢复原值。

在应用及 Nginx Compose 服务运行时，一键执行并生成本地 HTML 报告：

```bash
python apitest/run_all.py
```

默认 API 地址为 `http://localhost:5001`，数据库容器名为 `campusclaw-zzc-app-1`，使用开发预置的 `a_student`、`a_teacher` 账号。可通过 `APITEST_BASE_URL`、`APITEST_CONTAINER`、`APITEST_DATABASE` 和 `APITEST_USERNAME` / `APITEST_PASSWORD`、`APITEST_TEACHER_USERNAME` / `APITEST_TEACHER_PASSWORD` 指向其他环境；不要把生产凭据写入仓库。报告输出到 `apitest/reports/`，该目录已列入 `.gitignore`。测试要求本机可访问 Docker CLI 和已启动的目标服务；生成消息用例会调用实际问答后端。

## 教师解题引导与学生会话

启动时 SQLite 会幂等创建辅导配置、技能、会话及消息表，并为每个班级初始化一项启用的「解题引导」技能。教师可以在课堂页编辑本班提示词和技能；服务端以写事务保护至少一项启用解题引导技能的约束。提示词长度上限为 4,000 字符，技能名称上限为 80 字符、技能指导内容上限为 2,000 字符。教师提示/技能属于受限配置文本，不能替代认证、班级范围、引用核验等服务端约束。

学生可新建、继续和删除自己的持久化会话。会话只对所有者可见，教师也不能读取；学生转班后不能继续访问创建时属于旧班的会话。提问只从认证用户本班检索证据，不能通过请求参数指定班级或提交自带证据。回答完成生成、引用和证据支持校验，并复核学生身份及材料当前版本后才会写入并开始 SSE；页面逐步显示的只是已核验的完整回答切片，不是模型未核验草稿。遇到模型/引用错误、材料更新或会话权限变化时，请求失败且不会保存成功回答；重新打开会话可恢复完整持久化消息。

单轮“根据材料回答”和会话辅导都使用当前班级的教师提示词及启用的「解题引导」技能，并调用已配置的 AI 回答网关（`QA_PROVIDER=auto` 且设置 `QA_API_BASE_URL`、`QA_API_KEY` 和 `QA_API_MODEL` 时优先使用网关；否则沿用本地模型）。系统允许回答完整问题并给出最终结论。回答中的 `claims` 只标注由本班检索原文支持的内容；通用背景或解释与本班材料引用分开呈现，不伪造本班引用。若本班无相关命中或检索证据不足，已配置的网关会直接生成完整回答并原样展示，不附加网关来源说明；网关未配置/不可用时仍返回错误或依据不足，不会伪装为知识库回答。

辅导 API（均使用现有 Bearer Token）：

- `GET/PUT /api/tutoring/config`：教师读取或更新本班提示词；PUT JSON 为 `{"teacher_prompt":"..."}`。
- `GET/POST /api/tutoring/skills`、`PUT/DELETE /api/tutoring/skills/<id>`：教师管理本班技能；`kind` 为 `solution_guidance` 或 `other`。
- `GET/POST /api/tutoring/sessions`、`GET/DELETE /api/tutoring/sessions/<id>`：学生管理本人会话。
- `POST /api/tutoring/sessions/<id>/messages`：学生 JSON 请求 `{"question":"...","mode":"hybrid"}`；成功回答返回 SSE `meta`、`delta`、`citations`、`done` 事件。依据不足返回带 `insufficient_evidence` 状态的 JSON；认证、引用/生成错误和过期证据分别以明确的 HTTP 错误响应。

辅导配置、会话和消息保存在 `DATABASE_PATH` 对应的 SQLite 文件中，备份/恢复数据库时须一并保护学生对话数据。通过学生本人会话删除接口清理数据；不得将完整问题、回答或教师提示词写入运维日志。Nginx 对消息流路由关闭代理缓冲与压缩，并使用 180 秒超时；修改路由后可用 `docker compose config --quiet` 校验 Compose 配置，并检查 Nginx 配置语法。回滚辅导功能可将 `TUTORING_ENABLED=false`，隐藏辅导界面并禁用辅导 API，保留数据库数据。

```bash
python -m pytest -q                 # 确定性嵌入/生成替身，无模型下载
npm ci
npm run test:ui                     # 请求、引用、错误与竞态交互测试
python scripts/validate_knowledge_models.py # 单独的真实模型验收
openspec validate class-scoped-knowledge-retrieval --type change --strict
```

## 外部工具与 MCP

支持课堂 AI 调用管理员注册的 HTTP JSON 工具和外部 MCP（Streamable HTTP / stdio），教师在「工具与 MCP」中按班级启用。内置只读计算器可直接用于验证，所有工具默认停用。远程模型需支持 tool calling。

同时提供 `/mcp` 只读接口供外部 AI 客户端访问本班材料、知识检索和引用原文，沿用现有 Bearer 登录认证及班级隔离。Compose 自动启动独立 MCP 服务并由 Nginx 转发。完整配置、认证方式、限制和接入示例见 [外部工具与双向 MCP](docs/integrations.md)。
