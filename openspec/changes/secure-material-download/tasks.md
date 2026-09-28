## 1. 下载接口与服务端授权

- [x] 1.1 增加 `GET /api/materials/<material_id>/download` 路由，并复用现有认证装饰器；verify：未登录请求返回 HTTP 401 且响应不含文件内容或存储路径
- [x] 1.2 实现基于 session `class_id`、材料 ID 和 `index_status = 'indexed'` 的联合查询；verify：同班教师和学生可进入下载流程，A/B 班跨班请求返回 HTTP 403
- [x] 1.3 禁止使用客户端 `class_id`、`storage_path` 或任意路径作为授权/文件来源；verify：提交伪造 class ID、storage path、query、body 和 header 后仍只能访问 session 所属班级材料

## 2. 安全文件发送

- [x] 2.1 使用 Flask/Werkzeug 安全文件发送 API 返回附件，并使用数据库原始文件名作为下载名；verify：下载响应包含正确的附件 Content-Disposition、文件内容和 MIME 信息
- [x] 2.2 对存储路径执行根目录解析、普通文件存在性和子路径校验，阻止 `../`、绝对路径和存储根目录外符号链接；verify：路径穿越测试无法读取临时敏感文件或返回服务器绝对路径
- [x] 2.3 处理不存在材料、未索引材料和已删除文件；verify：这些场景返回稳定的 403/404/文件不可用错误，不返回堆栈、路径或文件内容

## 3. 前端下载入口

- [x] 3.1 在教师和学生材料列表项增加下载按钮，按钮只使用材料 ID 调用受保护下载接口；verify：页面源码和浏览器请求不包含 `storage_path` 或客户端 class ID
- [x] 3.2 实现浏览器附件下载、下载中状态和 401/403/404/网络错误反馈；verify：同班下载成功保存文件，错误响应显示可理解提示且不报告成功
- [x] 3.3 保持教师/学生都能下载本班材料，但不改变学生上传权限；verify：教师和学生下载测试均成功，学生直接上传仍返回 HTTP 403

## 4. 测试与集成

- [x] 4.1 增加 Flask 下载接口测试，覆盖登录校验、同班教师下载、同班学生下载和跨班 403；verify：`.venv/bin/python -m pytest -q` 覆盖所有授权场景并通过
- [x] 4.2 增加未索引、文件缺失、原始文件名和路径穿越测试；verify：测试确认只有 indexed 且位于存储根目录内的普通文件可以被发送
- [x] 4.3 增加前端下载按钮和错误反馈的页面/端到端测试；verify：教师和学生列表均出现下载操作，跨班材料不出现在列表中
- [x] 4.4 使用 Docker Compose 重建并验证下载功能；verify：`docker compose up --build -d` 成功，`GET /health` 成功，登录后同班下载返回文件
- [x] 4.5 执行 OpenSpec 严格校验；verify：运行 `openspec validate "secure-material-download" --type change --strict` 返回变更有效