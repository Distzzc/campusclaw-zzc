"""POST /api/tutoring/sessions/<session_id>/messages: reconcile response with DB."""
import json


def _parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        event_name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        if event_name and data_lines:
            events.append((event_name, json.loads("\n".join(data_lines))))
    return events


def run(ctx):
    token = ctx.require_student()
    session = ctx.require_session()
    question = f"请根据本班资料，给我一个关于“{ctx.search_query or '课程内容'}”的解题思考提示，并引导我推理，不要直接给出最终答案。"
    try:
        ctx.require_search()
        status, payload, headers = ctx.request(
            "POST", f"/api/tutoring/sessions/{session['id']}/messages", token=token,
            body={"question": question, "mode": "keyword"}, timeout=240,
        )
        ctx.require(status == 200, f"发送会话消息应返回 200，实际为 {status}")

        if "text/event-stream" in headers.get("Content-Type", ""):
            ctx.require(isinstance(payload, str), "SSE 响应应为文本")
            events = _parse_sse(payload)
            by_name = {}
            for event_name, data in events:
                by_name.setdefault(event_name, []).append(data)
            for expected in ("meta", "delta", "citations", "done"):
                ctx.require(expected in by_name, f"SSE 响应缺少 {expected} 事件")
            assistant_id = by_name["done"][-1].get("message_id")
            ctx.require(isinstance(assistant_id, int), "SSE done 缺少持久化消息 ID")
            assistant_text = "".join(item.get("text", "") for item in by_name["delta"])
            citations = by_name["citations"][-1].get("items")
            ctx.require(isinstance(citations, list), "SSE citations.items 应为数组")
            ctx.require(by_name["meta"][-1].get("message_id") == assistant_id,
                        "SSE meta 和 done 消息 ID 不一致")
        else:
            ctx.require(isinstance(payload, dict) and payload.get("status") == "insufficient_evidence",
                        "非 SSE 成功响应应明确报告依据不足")
            ctx.require(payload.get("citations") == [], "依据不足响应不应包含引用")
            assistant_id = None
            assistant_text = payload.get("answer", "")
            citations = []

        rows = ctx.query_db(
            "SELECT id,role,content,status,citations_json,error_code FROM tutoring_messages "
            "WHERE session_id=? ORDER BY id",
            (session["id"],),
        )
        user_rows = [item for item in rows if item["role"] == "user"]
        assistant_rows = [item for item in rows if item["role"] == "assistant"]
        ctx.require(len(user_rows) == 1 and user_rows[0]["content"] == question
                    and user_rows[0]["status"] == "completed",
                    "用户消息响应/数据库正文或完成状态不一致")
        ctx.require(len(assistant_rows) == 1 and assistant_rows[0]["status"] == "completed",
                    "数据库应持久化一条完整的已完成助手消息")
        assistant = assistant_rows[0]
        ctx.require(assistant["content"] == assistant_text,
                    "SSE/JSON 回答文本与数据库持久化正文不一致")
        stored_citations = json.loads(assistant["citations_json"])
        ctx.require(stored_citations == citations,
                    "响应引用列表与数据库持久化引用不一致")
        if assistant_id is not None:
            ctx.require(assistant["id"] == assistant_id, "SSE 消息 ID 与数据库记录不一致")
        for citation in citations:
            matches = ctx.query_db(
                "SELECT c.content_version,c.text,m.title,m.class_id FROM knowledge_chunks c "
                "JOIN materials m ON m.id=c.material_id WHERE c.id=? AND c.material_id=? "
                "AND c.content_version=m.content_version AND c.generation=m.index_generation "
                "AND c.class_id=? AND m.class_id=? AND m.index_status='indexed' "
                "AND m.search_index_status='indexed'",
                (citation["chunk_id"], citation["material_id"], ctx.user["class_id"], ctx.user["class_id"]),
            )
            ctx.require(len(matches) == 1, "回答引用未指向本班数据库当前可检索材料")
            row = matches[0]
            ctx.require(row["content_version"] == citation["content_version"]
                        and row["text"] == citation["excerpt"] and row["title"] == citation["title"],
                        "回答引用原文/版本/标题与当前数据库不一致")
    finally:
        # This API test is disposable: verify persistence first, then clean its
        # session and messages through the public owner-scoped endpoint.
        cleanup_status, cleanup, _ = ctx.request(
            "DELETE", f"/api/tutoring/sessions/{session['id']}", token=token,
        )
        ctx.require(cleanup_status == 200 and cleanup.get("status") == "deleted",
                    "测试完成后清理临时会话失败")
        remains = ctx.query_db("SELECT id FROM tutoring_sessions WHERE id=?", (session["id"],))
        ctx.require(not remains, "已删除的测试会话仍存在于 SQLite")
