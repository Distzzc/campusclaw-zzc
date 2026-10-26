## Purpose

为教师和学生提供仅限所属班级的正文索引、关键字、向量与混合检索，以及依据知识库内容生成的单轮问答。每条检索命中和回答引用都能追溯到原始材料的具体片段，资料不足时明确告知，且全过程保持班级权限隔离。

## ADDED Requirements

### Requirement: Authenticated users can search their class materials

系统 SHALL 为持有有效 JWT Bearer Token 的教师和学生提供材料内容检索能力。检索 SHALL 支持关键字和自然语言查询并按相关性返回命中片段；检索范围 MUST 由当前认证用户的服务端班级归属确定，客户端提供的 `class_id` MUST NOT 扩大或改变该范围。系统 MUST NOT 在搜索结果中返回其他班级的内容、元数据或可推断其存在的信息。

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
- **WHEN** 客户端未携带 Token，或使用无效、过期、已撤销的 Token 提交检索请求
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

系统 SHALL 对受支持材料完成正文提取、切分、向量嵌入及持久化入库，同时建立关键字索引，并保留每个片段与源材料、内容版本和班级的关联。首期 SHALL 支持 UTF-8 TXT、Markdown、文本型 PDF 和 DOCX。材料仅在原文件可用且片段、关键字及向量索引全部成功写入后 SHALL 被标记为可检索；正在处理或索引失败的材料 MUST NOT 出现在检索或问答证据中。重复索引同一材料 SHALL 可安全重试，且 MUST NOT 造成重复命中或遗留过期片段。索引失败 MUST NOT 影响已存储原文件的授权下载。

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

#### Scenario: Partial index writes are not published
- **WHEN** 正文片段或关键字索引已写入，但向量嵌入或向量入库失败
- **THEN** 三种检索与问答均不能使用该材料，原文件仍可由同班用户下载，重试成功后恢复可检索状态

#### Scenario: Existing materials can be backfilled and survive restart
- **WHEN** 对历史材料执行回填且索引成功，随后重启服务
- **THEN** 片段、关键字和向量索引仍可使用，来源与班级归属保持一致

### Requirement: The classroom interface displays search results with sources

已认证教师和学生的课堂界面 SHALL 提供三种检索模式选择、检索输入和独立的问答提交操作，并 SHALL 分别展示命中原文与生成回答。回答 SHALL 标注引用，用户 SHALL 能查看引用原文及访问受保护的原文件。界面 MUST 使用现有 Bearer 认证流程，不得将客户端过滤作为安全边界。加载、空结果、依据不足、服务失败与材料索引状态 SHALL 清晰呈现。

#### Scenario: User searches from the classroom interface
- **WHEN** 用户提交检索词并且服务端返回命中
- **THEN** 界面展示相关片段及各自材料标题和来源定位，并提供受保护的原材料访问入口

#### Scenario: Search has no results
- **WHEN** 检索成功且服务端返回空结果
- **THEN** 界面展示清晰的无匹配内容状态，不显示虚构的回答或引用

#### Scenario: Search request fails
- **WHEN** 检索接口返回认证、授权、参数或服务错误
- **THEN** 界面展示适当的错误反馈，不将失败响应当作成功结果，并保持页面可继续操作

#### Scenario: User reads an answer and its evidence
- **WHEN** 问答成功返回带引用的回答
- **THEN** 界面展示回答、引用编号、对应原文、材料标题与定位，点击来源时携带有效认证访问原文或下载文件

### Requirement: Search supports keyword vector and hybrid modes

系统 SHALL 支持 `keyword`、`vector`、`hybrid` 三种检索模式，未指定时 SHALL 使用 `hybrid`。关键字检索 SHALL 支持中文与英文词项；向量检索 SHALL 支持不依赖完全相同词面的语义匹配；混合检索 SHALL 综合两路候选并去除重复片段。所有模式 SHALL 先限定本班范围再选择候选，并在返回前核验来源权限与版本。有效查询无相关证据 SHALL 返回空结果；模式依赖失败 SHALL 返回服务错误，不得伪装成空结果或静默改用其他模式。

#### Scenario: Chinese keyword retrieval
- **WHEN** 本班已索引正文包含“光合作用”，用户用 keyword 模式查询“光合作用”
- **THEN** 返回包含匹配词项的原文与出处，即使原文没有空格分词也能检索

#### Scenario: Semantic retrieval
- **WHEN** 本班材料说明“植物通过光合作用制造养分”，用户以 vector 模式查询“植物如何利用光能合成有机物”
- **THEN** 系统能按语义相关性返回对应材料，不要求查询与正文逐字相同

#### Scenario: Hybrid results combine and deduplicate candidates
- **WHEN** keyword 和 vector 分别召回相关片段，部分片段同时被两路命中
- **THEN** hybrid 综合两路相关顺序返回结果，同一版本的同一片段仅出现一次，默认请求使用相同行为

#### Scenario: Invalid parameters and unavailable dependencies
- **WHEN** 用户提交未知 mode、非字符串 query、空白或超长 query，或非整数及越界 limit
- **THEN** 返回 HTTP 400，不执行检索

#### Scenario: Vector dependency is unavailable
- **WHEN** 向量依赖不可用且用户请求 vector 或 hybrid
- **THEN** 返回 HTTP 503 和安全错误提示；用户另行请求 keyword 时，在其依赖健康的情况下仍可正常检索已完整索引的材料

### Requirement: Source passages are retrievable with class and version checks

系统 SHALL 为每条命中和回答引用提供稳定片段标识、内容版本、原文、材料标识、标题及页码/章节/段落或片段序号。用户 SHALL 能查看完整引用片段并访问原文件。原文读取 SHALL 校验 JWT、当前班级及当前可检索版本；过期引用 MUST NOT 被悄悄指向新内容，其他班级引用 MUST NOT 泄露其内容或存在信息。

#### Scenario: User follows a current citation
- **WHEN** 同班用户打开有效引用
- **THEN** 返回与检索命中或问答证据一致的完整原文、版本及源内定位，并提供受保护的原文件访问入口

#### Scenario: Citation is stale or belongs to another class
- **WHEN** 用户访问已被替换的片段版本、不存在的片段或其他班级的片段
- **THEN** 返回统一的不可用响应且不返回原文或材料元数据，不自动改指向新版本

### Requirement: Answers are grounded in current class evidence

系统 SHALL 为已认证师生提供单轮知识库问答，支持与检索相同的三种模式并默认使用 hybrid。系统 MUST 仅将当前班级中已授权、当前版本的检索证据用于回答，不使用全库检索、联网搜索或模型常识补充无依据事实。回答中的知识性陈述 SHALL 标注能够支持该陈述的引用；引用 SHALL 仅来自本次证据集合，并附对应原文和出处。材料中的指令 MUST 被视为资料文本，不得改变权限、检索范围或系统回答规则。

#### Scenario: Answer cites supporting knowledge
- **WHEN** 用户提出问题且本班检索证据足以回答
- **THEN** 返回带引用的回答，引用与本次证据中的当前原文一致，用户可核验每项知识性陈述的依据

#### Scenario: Evidence is missing or insufficient
- **WHEN** 本班无命中，或命中内容不足以支持回答
- **THEN** 明确返回“本班知识库暂无足够依据”，不生成无依据答案、不编造引用，也不使用其他班级材料补足

#### Scenario: Cross-class data never enters generation
- **WHEN** 用户伪造 class_id、片段标识或在问题中要求使用其他班级资料
- **THEN** 生成上下文只包含服务端核验的本班检索证据，回答与引用不披露其他班级信息

#### Scenario: Material content contains instructions
- **WHEN** 检索证据中包含要求忽略规则、查询其他班级或虚构出处的文本
- **THEN** 系统不执行这些指令，仍按本班证据及引用要求回答，无法得到可靠答案时返回依据不足

#### Scenario: Model or citation validation fails
- **WHEN** 生成模型未配置、不可用、超时，或生成结果含不存在的引用、缺失引用、过期证据或无支持依据的陈述
- **THEN** 返回明确的服务错误或依据不足状态，不展示未经核验的成功答案，不影响用户独立使用正常工作的检索功能

#### Scenario: Authentication and input checks apply to questions
- **WHEN** 问答请求使用无效、过期或撤销的 Token，或有效用户提交空白、非字符串、超长问题或无效模式
- **THEN** 分别返回 HTTP 401 或 HTTP 400，且不检索材料或调用生成模型
