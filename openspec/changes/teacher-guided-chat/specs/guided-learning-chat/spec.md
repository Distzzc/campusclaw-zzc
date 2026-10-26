## Purpose

为课堂提供由教师配置的解题引导方式，以及学生本人可管理的持久化辅导会话。每次辅导均先依据本班知识库生成并核验回答和出处，再流式展示已核验内容，帮助学生推理而不直接替学生给出最终答案。

## ADDED Requirements

### Requirement: Teachers manage class prompts and solution-guidance skills
系统 SHALL 允许已认证教师设置本班辅导提示词，并创建、查看、编辑、启用、停用和删除本班技能。技能 MUST 包含名称、类型和指导内容；类型至少支持「解题引导」。每个班级 MUST 始终至少有一项启用的「解题引导」技能，服务端 MUST 原子地执行该不变量，拒绝停用或删除最后一项。解题引导内容 MUST 要求通过澄清问题、提示、步骤拆解或检查学生思路帮助其推理，且 MUST NOT 直接代写或揭示问题的最终答案。配置文本 SHALL 有明确长度限制，并作为不可信指令数据处理；不得改变认证、班级范围、证据核验、引用或安全约束。

#### Scenario: Teacher configures class coaching behavior
- **WHEN** 教师为其班级保存有效提示词或「解题引导」技能
- **THEN** 后续本班学生新发起的辅导请求使用该班当前有效配置，其他班级配置不受影响

#### Scenario: Required guidance skill cannot be removed
- **WHEN** 教师尝试停用或删除本班最后一项启用的「解题引导」技能
- **THEN** 服务端拒绝变更并保留原有有效技能配置

#### Scenario: Student or other-class teacher attempts configuration changes
- **WHEN** 学生提交配置变更，或教师尝试读取或修改其他班级配置
- **THEN** 服务端拒绝请求且不泄露配置内容

#### Scenario: Prompt attempts to override safety or evidence rules
- **WHEN** 教师配置的提示词或技能内容要求忽略引用、使用外班资料或给出未经支持的结论
- **THEN** 系统仍强制执行身份、班级、知识库证据和引用约束，并继续遵守解题引导不直接给出最终答案的要求

### Requirement: Students own persistent private tutoring sessions
系统 SHALL 允许已认证学生创建会话、列出自己的会话、读取会话消息并继续提问。会话、提问、已完成回答和引用 SHALL 持久化；服务端 MUST 将会话关联到创建学生，并在每次读取、续问或删除操作中验证当前学生所有权。其他学生和教师 MUST NOT 读取或修改该会话；客户端提供的用户或班级标识 MUST NOT 改变所有权或访问范围。已认证教师 SHALL 能管理本班配置，但不得因此取得学生会话内容访问权。

#### Scenario: Student creates and resumes a session
- **WHEN** 学生创建会话并在之后读取或继续该会话
- **THEN** 系统返回该学生已保存的会话及消息，并按时间顺序呈现

#### Scenario: Another student or teacher requests a private session
- **WHEN** 非会话所有者请求读取、续问或修改该会话
- **THEN** 服务端拒绝访问且不泄露会话是否存在或其内容

#### Scenario: Client spoofs session ownership or class
- **WHEN** 学生在会话请求中提交其他用户或班级标识
- **THEN** 服务端忽略这些字段并仅按认证身份授权，或将额外字段作为无效输入拒绝

#### Scenario: Teacher manages configuration without reading student history
- **WHEN** 教师访问本班辅导管理功能
- **THEN** 教师只能访问本班提示词和技能，不能列出或读取学生私有会话

### Requirement: Tutoring responses retrieve and validate current class evidence first
学生每次提交问题时，系统 MUST 使用认证学生当前所属班级检索知识库，并 MUST 在调用回答生成前完成证据选择。系统 MUST NOT 接受客户端指定的班级、证据片段或引用作为授权依据；回答只能使用服务端确认的本班、当前可检索证据。回答 SHALL 遵循本班有效提示词与至少一项启用的「解题引导」技能，优先提供提问、提示和分步推理，不直接给出最终答案。每项事实性陈述 MUST 有可核验引用；引用 MUST 指向本次检索的当前材料版本。资料不足时 MUST 明确告知并不得输出无依据答案。

#### Scenario: Student asks a question supported by class materials
- **WHEN** 学生在会话中提交问题且本班知识库包含足够的当前证据
- **THEN** 系统基于该证据返回符合解题引导方式的辅导内容，并为事实性陈述提供对应来源出处

#### Scenario: No sufficient class evidence exists
- **WHEN** 本班检索不到足够证据支持辅导内容
- **THEN** 系统返回明确的依据不足状态及空引用，不以模型常识或其他班级资料补足

#### Scenario: User attempts cross-class retrieval or supplies fake evidence
- **WHEN** 学生要求访问其他班级资料，或请求携带伪造证据/引用
- **THEN** 生成上下文只包含当前学生班级内经服务端核验的证据，伪造数据不被采用

#### Scenario: Prompt or source contains an instruction injection
- **WHEN** 提示词、技能、问题或知识库材料包含要求越权、忽略安全规则或编造来源的指令
- **THEN** 系统将其作为受限文本处理，不执行这些要求，并保持班级隔离、证据核验及引导式回答约束

#### Scenario: Evidence changes before an answer is committed
- **WHEN** 生成过程中引用材料的版本、可检索状态或学生授权发生变化
- **THEN** 系统丢弃该回答，不保存或流式发送过期内容，并返回可理解的可重试错误

### Requirement: Validated tutoring answers are delivered as a stream
系统 MUST 在开始发送回答正文前完成生成、逐项证据支持核验、引用校验和当前授权/版本复核。随后 SHALL 通过可被浏览器逐步读取的流式响应发送已核验回答及引用；流中 MUST NOT 包含未校验的模型草稿或部分生成内容。每个已完成回答及其引用 MUST 与学生问题一并持久化。依据不足、生成失败、校验失败或流建立前的授权失败 MUST 返回明确状态，不得伪装为成功流；断开连接不得留下标记为已完成但缺少正文或引用的消息。

#### Scenario: Verified response is streamed with citations
- **WHEN** 学生问题得到充分证据支持且回答和引用均通过核验
- **THEN** 浏览器可逐步读取回答内容及其引用，最终完成事件与持久化的完整消息一致

#### Scenario: Unverified draft must never be displayed
- **WHEN** 模型生成内容尚未完成引用、事实支持或版本校验
- **THEN** 流中不发送该草稿的任何正文

#### Scenario: Generation or validation fails
- **WHEN** 模型不可用、超时、格式无效、引用无效或证据支持核验失败
- **THEN** 系统返回明确错误或依据不足状态，不持久化为成功回答，也不发送未校验内容

#### Scenario: Client disconnects during delivery
- **WHEN** 浏览器在接收已校验回答时断开连接
- **THEN** 服务端不会留下不完整的成功消息；学生之后读取会话时只能看到完整成功消息或明确失败状态

### Requirement: Tutoring interface exposes role-appropriate controls and source provenance
课堂界面 SHALL 为教师提供本班提示词和技能管理入口，并为学生提供会话新建、会话列表、继续提问和流式回答展示。学生答案 SHALL 清晰区分辅导提示、依据不足及错误状态，并展示引用编号、材料标题和定位；学生 SHALL 能通过受保护的引用接口核验原文。前端 MUST 使用现有 Bearer 认证流程，且 MUST NOT 依靠隐藏控件作为授权边界。窄屏布局和流中断状态 SHALL 可理解、可恢复。

#### Scenario: Teacher manages class prompt and skills
- **WHEN** 教师进入课堂辅导管理区
- **THEN** 界面展示并允许其查看、更新本班提示词和技能，同时阻止移除最后一项启用的解题引导技能

#### Scenario: Student uses a tutoring session
- **WHEN** 学生打开课堂辅导区并新建或继续自己的会话
- **THEN** 界面提供会话消息、提问输入和可访问的流式状态，且仅显示该学生有权读取的会话

#### Scenario: Student verifies a citation
- **WHEN** 学生选择回答中的引用
- **THEN** 界面使用认证请求显示匹配的当前原文及材料定位，过期或不可访问引用不会被替换为其他内容

#### Scenario: Stream or request fails
- **WHEN** 网络中断、服务错误或认证过期导致流式交互失败
- **THEN** 界面显示明确状态，不把部分文本标记为完成答案，并允许按会话状态重试或重新登录