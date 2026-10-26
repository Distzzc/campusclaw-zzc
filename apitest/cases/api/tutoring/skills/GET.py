"""GET /api/tutoring/skills: verify enabled skills against the database."""


def run(ctx):
    token = ctx.require_teacher()
    status, payload, _ = ctx.request("GET", "/api/tutoring/skills", token=token)
    ctx.require(status == 200 and isinstance(payload, dict), f"技能读取应为 200 JSON，实际为 {status}")
    api_skills = payload.get("skills")
    ctx.require(isinstance(api_skills, list), "skills 响应字段应为数组")
    db_skills = ctx.query_db(
        "SELECT id,name,kind,instructions,enabled FROM tutoring_skills WHERE class_id=? ORDER BY id",
        (ctx.teacher["class_id"],),
    )
    normalized = [{key: skill[key] for key in ("id", "name", "kind", "instructions", "enabled")}
                  for skill in api_skills]
    ctx.require(normalized == db_skills, "技能列表响应与 SQLite 记录不一致")
    ctx.require(any(item["kind"] == "solution_guidance" and item["enabled"] for item in db_skills),
                "至少应有一项启用的解题引导技能")
