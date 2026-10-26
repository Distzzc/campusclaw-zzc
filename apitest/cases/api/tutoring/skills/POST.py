"""POST /api/tutoring/skills: persist a disposable test skill and remove it."""
import uuid


def run(ctx):
    token = ctx.require_teacher()
    class_id = ctx.teacher["class_id"]
    name = f"apitest-{uuid.uuid4().hex[:12]}"
    body = {"name": name, "kind": "other", "instructions": "用于验证 API 与数据库一致性", "enabled": False}
    status, payload, _ = ctx.request("POST", "/api/tutoring/skills", token=token, body=body)
    ctx.require(status == 201 and isinstance(payload, dict), f"新增技能应返回 201，实际为 {status}")
    skill = payload.get("skill", {})
    ctx.require(skill.get("name") == name and skill.get("kind") == body["kind"]
                and skill.get("instructions") == body["instructions"] and skill.get("enabled") == 0,
                "新增技能响应字段与请求不一致")
    ctx.created_skill_id = skill.get("id")
    ctx.require(isinstance(ctx.created_skill_id, int), "新增技能响应缺少数据库 ID")
    try:
        rows = ctx.query_db(
            "SELECT id,class_id,name,kind,instructions,enabled FROM tutoring_skills WHERE id=?",
            (ctx.created_skill_id,),
        )
        ctx.require(rows == [{"id": ctx.created_skill_id, "class_id": class_id, "name": name,
                             "kind": body["kind"], "instructions": body["instructions"], "enabled": 0}],
                    "新增技能响应与 SQLite 持久化记录不一致")
        get_status, get_payload, _ = ctx.request("GET", "/api/tutoring/skills", token=token)
        ctx.require(get_status == 200 and any(item.get("id") == ctx.created_skill_id
                                               for item in get_payload.get("skills", [])),
                    "技能列表未包含刚创建的测试技能")
    finally:
        deleted, delete_payload, _ = ctx.request(
            "DELETE", f"/api/tutoring/skills/{ctx.created_skill_id}", token=token
        )
        ctx.require(deleted == 200 and delete_payload.get("status") == "deleted", "清理测试技能失败")
        remains = ctx.query_db("SELECT id FROM tutoring_skills WHERE id=?", (ctx.created_skill_id,))
        ctx.require(not remains, "已删除测试技能仍存在于数据库")
