## Context

现有 CampusClaw 是 Flask + SQLite 单体应用，用户通过 Flask session 登录，材料元数据保存在 SQLite，原始文件保存在配置的 `STORAGE_PATH` 下。当前 `/api/materials` 和材料详情接口只返回元数据，没有文件下载路由；前端材料列表也没有下载操作。行为契约见 `specs/secure-material-download/spec.md`。

## Goals / Non-Goals

**Goals:**

- 增加教师和学生都可使用的受保护下载接口。
- 在数据库查询层完成 session 用户、材料 `class_id` 和 `index_status` 的联合授权。
- 只从受控存储根目录发送文件，并使用数据库原始文件名作为响应下载名。
- 为现有前端材料列表增加下载按钮和错误反馈。
- 用测试覆盖未登录、同班、跨班、状态过滤、文件缺失和路径穿越。

**Non-Goals:**

- 不改变登录、session 签发、角色定义、班级归属或上传入库流程。
- 不增加临时分享链接、公开下载、批量下载、预览转换或下载审计报表。
- 不引入新的对象存储或 CDN；继续使用当前 Docker Compose 持久化材料目录。

## Decisions

### 下载接口与授权查询

- 增加 `GET /api/materials/<int:material_id>/download`。路由先要求认证，再使用当前用户 session 的 `class_id` 查询材料：`id = ? AND class_id = ? AND index_status = 'indexed'`。相较先查 ID 再在 Python 中过滤，这能让跨班记录不进入业务层。
- 学生和教师共享下载权限，角色只影响上传，不影响同班已索引材料的读取/下载。未认证返回 401，已认证但材料不在授权范围内返回 403；错误响应不包含 `storage_path`。
- 不接受 query string、表单、JSON 或 header 中的 `class_id`/`storage_path` 作为授权或文件来源。材料文件路径只能来自服务端数据库记录，并经过根目录校验。

### 安全文件发送

- 使用 Flask/Werkzeug 的安全文件发送 API（例如 `send_file`），将 `as_attachment=True` 并设置数据库原始文件名作为 `download_name`。
- 将数据库路径解析为 `Path`，解析配置的 `STORAGE_PATH` 根目录和目标路径，并要求目标路径是根目录的后代；拒绝绝对路径、`..` 穿越和解析后位于根目录外的符号链接。
- 在发送前检查文件存在且为普通文件；不存在返回稳定的文件不可用错误，不把 Python 异常或绝对路径返回给客户端。必要时将数据库中的存储引用改为相对存储键，而不是信任历史绝对路径。

### 前端下载入口

- 在现有材料列表项中增加下载按钮，使用材料 ID 调用 `/api/materials/<id>/download`。不把服务端 `storage_path` 放入 DOM 或 URL。
- 对文件响应使用浏览器下载行为；若前端使用 `fetch`，从 `Content-Disposition` 或已知元数据创建 Blob 下载，并统一处理 401/403/404/5xx。更简单的方案是将受保护接口作为按钮链接，让浏览器直接处理附件响应。
- 下载失败显示通知，401 回登录页，403 显示“无权下载该材料”，文件不可用显示可理解的重试提示。

### 测试与部署

- Flask 测试使用临时 SQLite 和临时材料目录，创建 A/B 班账号及材料文件，断言响应状态、内容、`Content-Disposition` 文件名和不存在路径不泄露。
- 增加路径穿越测试：构造 `../`、绝对路径和存储根目录外的符号链接，确认不会发送敏感文件。
- 继续通过 Docker Compose 挂载材料数据卷；下载接口不新增端口、服务或密钥配置。

## Risks / Trade-offs

- [Risk] 历史记录中的 `storage_path` 可能是绝对路径或已被移动 → 发送前统一解析并限制在当前 `STORAGE_PATH`，缺失文件返回稳定错误。
- [Risk] 文件名包含特殊字符可能造成响应头问题 → 使用框架的 `download_name` 编码能力并限制为 basename，不手工拼接 Content-Disposition。
- [Risk] 仅用前端隐藏下载按钮会被直接请求绕过 → 所有权限判断在下载路由的数据库查询和 session 校验中完成，并加入跨班否定测试。
- [Risk] 大文件占用应用进程资源 → 使用框架安全文件发送和服务器支持的流式响应；本变更不承诺断点续传。

## Migration Plan

1. 增加下载路由和服务端路径校验，不改变已有 API 响应。
2. 增加下载按钮和前端错误处理，重新构建 Compose 镜像。
3. 执行同班下载、跨班拒绝、未索引、缺失文件和路径穿越测试，再在本地容器验证下载响应。
4. 若需要回滚，移除下载按钮和路由即可，现有上传记录及材料文件无需迁移。

## Open Questions

- 当前需求未规定下载审计日志、速率限制或文件大小上限；这些可在后续安全运维变更中单独定义，不影响本次接口和班级授权契约。