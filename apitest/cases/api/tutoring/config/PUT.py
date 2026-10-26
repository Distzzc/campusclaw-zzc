"""PUT /api/tutoring/config: verify prompt persistence, then restore original config."""
import uuid


def run(ctx):
    token = ctx.require_teacher()
    before = ctx.config_before
    if before is None:
        raise RuntimeError("先执行 GET /api/tutoring/config")
    class_id = ctx.teacher["class_id"]
    probe = f"apitest-prompt-{uuid.uuid4().hex}：循序提问"
    status, payload, _ = ctx.request("PUT", "/api/tutoring/config", token=token,
                                     body={"teacher_prompt": probe})
    ctx.require(status == 200 and payload.get("teacher_prompt") == probe,
                f"提示词更新响应与请求不一致（HTTP {status}）")
    try:
        rows = ctx.query_db("SELECT teacher_prompt FROM tutoring_settings WHERE class_id=?", (class_id,))
        ctx.require(len(rows) == 1 and rows[0]["teacher_prompt"] == probe,
                    "提示词 PUT 成功响应与 SQLite 持久化值不一致")
        check_status, check_payload, _ = ctx.request("GET", "/api/tutoring/config", token=token)
        ctx.require(check_status == 200 and check_payload.get("teacher_prompt") == probe,
                    "再次读取配置未返回已保存提示词")
    finally:
        restored_status, restored, _ = ctx.request(
            "PUT", "/api/tutoring/config", token=token,
            body={"teacher_prompt": before["teacher_prompt"]},
        )
        ctx.require(restored_status == 200 and restored.get("teacher_prompt") == before["teacher_prompt"],
                    "测试结束时未能恢复原有教师提示词")
        restored_rows = ctx.query_db("SELECT teacher_prompt FROM tutoring_settings WHERE class_id=?", (class_id,))
        ctx.require(len(restored_rows) == 1 and restored_rows[0]["teacher_prompt"] == before["teacher_prompt"],
                    "恢复后的提示词与数据库不一致")
