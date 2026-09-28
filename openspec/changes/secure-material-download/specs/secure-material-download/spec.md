## Purpose

为课堂材料提供受认证和班级边界保护的文件下载能力，使教师和学生可以取得本班已成功入库的原始材料，同时阻止跨班访问、路径穿越和未完成材料被下载。

## ADDED Requirements

### Requirement: Authenticated users can download indexed materials from their class

系统 SHALL 提供受保护的材料下载接口，例如 `GET /api/materials/<material_id>/download`。教师和学生在通过认证后，只有当材料属于其 session 中的实际 `class_id` 且 `index_status = 'indexed'` 时，才 SHALL 获得文件内容。成功响应 SHALL 使用数据库保存的原始文件名作为下载文件名。

#### Scenario: Same-class teacher downloads material
- **WHEN** 已认证教师下载自己班级中已索引且文件存在的材料
- **THEN** 服务端返回文件内容和附件下载响应，并使用数据库中的原始文件名

#### Scenario: Same-class student downloads material
- **WHEN** 已认证学生下载自己班级中已索引且文件存在的材料
- **THEN** 服务端返回文件内容和附件下载响应，并不要求学生具备教师角色

#### Scenario: Unauthenticated download is rejected
- **WHEN** 没有有效 session 的客户端请求材料下载接口
- **THEN** 服务端返回 HTTP 401，且不返回文件内容或存储路径

### Requirement: Download authorization enforces the server-side class boundary

服务端 MUST 从当前认证 session 或等价可信认证上下文获取用户的 `class_id`，并 MUST 将材料资源的 `class_id` 与该值进行授权校验。服务端 MUST NOT 使用客户端提交的 `class_id` 或 `storage_path` 作为授权依据。

#### Scenario: Cross-class download is forbidden
- **WHEN** A 班用户请求 B 班材料的下载接口
- **THEN** 服务端返回 HTTP 403，且不返回文件内容、真实路径或材料元数据

#### Scenario: Spoofed class identifier does not grant access
- **WHEN** A 班用户在查询参数、请求体或请求头中提交 B 班的 `class_id`
- **THEN** 服务端忽略该不可信值，仍按 A 班身份授权，并拒绝 B 班材料下载

#### Scenario: Client-supplied storage path is ignored
- **WHEN** 客户端提交一个指向任意文件的 `storage_path`
- **THEN** 服务端不使用该路径发送文件，且下载结果只由服务端材料记录和授权范围决定

### Requirement: Invalid or unavailable materials cannot be downloaded

系统 SHALL 仅允许下载 `index_status = 'indexed'` 的材料。材料记录不存在、属于其他班级、索引未完成或原始文件不存在时，服务端 MUST 拒绝下载并不得暴露服务器文件系统路径。

#### Scenario: Non-indexed material is rejected
- **WHEN** 已认证用户请求 `pending` 或 `failed` 材料
- **THEN** 服务端拒绝下载且不返回文件内容

#### Scenario: Missing material record is rejected
- **WHEN** 已认证用户请求不存在的材料 ID
- **THEN** 服务端返回不可访问错误，且不泄露该 ID 是否曾经存在

#### Scenario: Missing stored file is reported safely
- **WHEN** 材料记录已索引但其存储文件不存在
- **THEN** 服务端返回文件不可用错误，且不返回绝对路径、目录结构或堆栈信息

### Requirement: File delivery prevents path traversal

服务端 SHALL 只允许从配置的材料存储根目录及其受控子路径发送文件。下载实现 MUST 验证最终解析路径仍位于该根目录内，并 MUST 使用安全文件发送 API；客户端不得通过材料 ID、原始文件名或路径片段读取任意服务器文件。

#### Scenario: Traversal filename cannot escape storage root
- **WHEN** 材料记录或请求中包含 `../`、绝对路径或符号链接指向存储根目录外的路径
- **THEN** 服务端拒绝下载且不读取存储根目录外的文件

#### Scenario: Valid material is sent from controlled storage
- **WHEN** 材料记录指向存储根目录内存在的已索引文件
- **THEN** 服务端发送该文件，并将下载名限制为数据库记录中的 basename

### Requirement: The class material list exposes a protected download action

前端材料列表 SHALL 为当前用户可访问的每条材料提供下载按钮或等价操作，并 SHALL 调用受保护的下载接口。教师和学生的按钮都只能针对服务端返回的本班材料显示；下载失败时 SHALL 展示错误反馈而不是伪造成功。

#### Scenario: Download button is shown for class material
- **WHEN** 已认证教师或学生加载出本班材料列表
- **THEN** 每条可下载材料显示下载操作，且操作指向该材料 ID 的受保护下载接口

#### Scenario: Download failure is visible
- **WHEN** 下载请求返回 401、403、404 或文件不可用错误
- **THEN** 前端显示对应错误并保持页面可用，不报告下载成功