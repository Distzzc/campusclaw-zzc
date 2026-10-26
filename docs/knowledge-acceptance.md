# 班级知识库验收记录

验收日期：2026-09-30。变更：`class-scoped-knowledge-retrieval`。

## 自动化与运行验证

- Python：62 项测试通过，包含原有登录/材料回归、迁移、四类正文提取、切分、索引重试/代次隔离、三种检索、引用与问答校验。
- 前端：10 项 jsdom 交互测试通过，覆盖 Bearer 请求、转义、引用原文、无结果/依据不足、401/404/409/502/503、请求乱序和退出清理。
- OpenSpec 严格校验及 `git diff --check` 通过。
- 独立镜像 `campusclaw-knowledge-validation` 构建成功，默认 Gunicorn 启动后 `/health` 返回正常。
- 一次性容器在 `--network none` 下写入索引，随后销毁并重新创建容器：SQLite/FTS 与 Chroma 索引均可检索，原文及下载可访问。
- 关闭 `KNOWLEDGE_ENABLED` 后新增界面/API 隐藏，原文件仍可授权下载。
- Compose 配置检查及 Nginx 配置语法检查通过。
- 当前没有可连接的浏览器，未进行实际浏览器目视检查；前端交互验证使用 jsdom。

## 真实模型

- 嵌入：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，修订 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`，384 维、输入上限 128 token。
- 生成：`Qwen/Qwen2.5-1.5B-Instruct`，修订 `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`，本地加载、禁止远程模型代码；完整问答包含草稿生成、引文选择与独立支持性核验。
- 模型文件约 3.35 GiB。本机物理内存 24 GiB；下表内存为生成子进程实测峰值，不含宿主应用、向量索引及其他进程。
- 中文同义问题与测试原文余弦相似度约 0.832，不相关查询约 -0.023，保留默认阈值 0.35。该阈值通过当前固定样例，不代表其他课程语料都已校准。

| 环境 | 首次索引 | 有依据问答 | 推理进程峰值内存 | 样例通过 |
| --- | --- | --- | --- | --- |
| macOS 本机 | 3.67 秒 | 16.8 秒 | 3.26 GiB | 8/8 |
| Linux 断网容器 | 2.668 秒 | 42.123 秒 | 3.83 GiB | 8/8 |

问答样例：“光合作用利用什么原料，产生什么物质？”

输出：“光合作用利用二氧化碳和水，产生有机物并释放氧气。 [1]”

引用指向实际入库原文，重新通过受保护来源接口获取的正文与引用一致。额外样例覆盖中文关键字、同义向量/混合查询、不相关问题、资料不足、问题指令诱导与材料指令注入。固定样例验收不能证明所有问题都不会漏答或误判；部署其他模型或课程语料需重新校准。

复现命令：

```bash
python -m pytest -q
npm ci && npm run test:ui
python scripts/validate_knowledge_models.py
openspec validate class-scoped-knowledge-retrieval --type change --strict
```

完整本机报告：`data/model-acceptance.json`。完整容器报告：`data/container-validation/model-acceptance.json`。二者均为本地验收数据，不进入 Git。

## 部署边界

本轮仅构建独立验证镜像并运行自动删除的测试容器，没有更新或重启现有 Compose 服务。正式启用需按 README 准备模型路径、备份原数据卷，再重新构建并更新目标 Compose 容器；历史材料通过 `reindex-materials` 回填。
