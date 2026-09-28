## Purpose

为教师和学生提供仅限所属班级的材料内容检索，并让每条命中都能追溯到原始材料中的具体片段。检索只呈现有来源依据的材料内容，不将检索结果伪装成无来源的生成式答案。

## ADDED Requirements

### Requirement: Authenticated users can search their class materials

系统 SHALL 为已认证教师和学生提供材料内容检索能力。检索 SHALL 支持自然语言查询并按相关性返回命中片段；检索范围 MUST 由当前认证身份中的班级归属确定，客户端提供的 `class_id` MUST NOT 扩大或改变该范围。系统 MUST NOT 在搜索结果中返回其他班级的内容、元数据或可推断其存在的信息。

#### Scenario: User searches relevant content in their class
- **WHEN** 已认证用户提交非空查询
- **THEN** 系统仅从该用户所属班级已成功索引的材料中按相关性返回结果

#### Scenario: Client cannot change the search class
- **WHEN** 用户在检索请求中附带其他班级的 `class_id`
- **THEN** 系统忽略该值，并仅按认证身份中的班级执行检索

#### Scenario: Cross-class content is not disclosed
- **WHEN** 检索词只匹配其他班级的材料，或同时匹配本班与其他班材料
- **THEN** 响应只包含本班命中，且不暴露其他班级的命中数、标题、片段或材料标识

#### Scenario: Unauthenticated search is rejected
- **WHEN** 未认证客户端提交检索请求
- **THEN** 服务端返回 HTTP 401，且不返回任何检索结果或材料元数据

#### Scenario: Invalid search query is rejected
- **WHEN** 已认证用户提交空白查询、缺失查询或超过系统限制的查询
- **THEN** 服务端返回 HTTP 400，且不执行检索

### Requirement: Search results provide verifiable source provenance

每条检索命中 SHALL 返回可核验的来源信息，包括来源材料标识、材料标题、命中原文片段和源内定位信息。定位信息 SHALL 在格式支持时使用页码或章节/标题；无法提取结构化定位时 SHALL 提供稳定的片段序号。响应 MUST 将原文命中与其来源关联，不得生成或改写成无出处的材料内容。

#### Scenario: Search result identifies its source
- **WHEN** 搜索命中一段已索引材料内容
- **THEN** 结果包含原文片段、对应材料标题与标识，以及可定位到原材料的页码、章节或稳定片段序号

#### Scenario: Source location is unavailable in the original format
- **WHEN** 命中文本来自不包含页码或章节结构的材料
- **THEN** 结果仍提供稳定片段序号和材料标识，使用户能够区分并追溯该命中

#### Scenario: No material matches the query
- **WHEN** 用户提交有效查询但本班没有相关的已索引内容
- **THEN** 系统返回空结果集，不编造回答或来源

### Requirement: Material indexing is complete and class-scoped before search

系统 SHALL 在材料检索索引中保留每个内容片段与其源材料及班级的关联。材料仅在原文件可用且其可检索内容成功写入索引后 SHALL 被标记为可检索；正在处理或索引失败的材料 MUST NOT 出现在检索结果中。重复索引同一材料 SHALL 可安全重试，且 MUST NOT 造成重复命中或遗留过期片段。

#### Scenario: Newly uploaded material becomes searchable after indexing
- **WHEN** 教师上传本班可处理的材料且内容索引成功完成
- **THEN** 同班用户随后可以检索到其内容，并得到指向该材料的来源信息

#### Scenario: Failed or pending material is not searchable
- **WHEN** 材料内容提取或检索索引仍在处理中或失败
- **THEN** 该材料不出现在检索结果中，且失败状态不会被报告为检索可用

#### Scenario: Retrying indexing does not duplicate or retain stale chunks
- **WHEN** 系统对已处理材料重新执行索引
- **THEN** 搜索只返回该材料当前内容对应的片段，每个命中不因重试而重复，旧内容不再可检索

#### Scenario: Unsupported or unreadable material reports indexing failure
- **WHEN** 上传的材料格式不受支持或正文无法读取
- **THEN** 系统将材料标记为不可检索的失败状态，并向有权用户提供可理解的处理状态而非伪造空内容索引

### Requirement: The classroom interface displays search results with sources

已认证教师和学生的课堂界面 SHALL 提供检索输入及提交操作，并 SHALL 展示服务端返回的命中片段和来源定位。来源 SHALL 可用于打开或下载同班原始材料；界面 MUST 保留服务端的认证和班级授权，不得将客户端过滤作为安全边界。加载、空结果和失败状态 SHALL 清晰呈现。

#### Scenario: User searches from the classroom interface
- **WHEN** 用户提交检索词并且服务端返回命中
- **THEN** 界面展示相关片段及各自材料标题和来源定位，并提供受保护的原材料访问入口

#### Scenario: Search has no results
- **WHEN** 检索成功且服务端返回空结果
- **THEN** 界面展示清晰的无匹配内容状态，不显示虚构的回答或引用

#### Scenario: Search request fails
- **WHEN** 检索接口返回认证、授权、参数或服务错误
- **THEN** 界面展示适当的错误反馈，不将失败响应当作成功结果，并保持页面可继续操作