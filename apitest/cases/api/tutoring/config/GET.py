"""GET /api/tutoring/config: compare teacher-visible config with class DB rows."""


def run(ctx):
    token = ctx.require_teacher()
    status, payload, _ = ctx.request("GET", "/api/tutoring/config", token=token)
    ctx.require(status == 200 and isinstance(payload, dict), f"配置读取应为 200 JSON，实际为 {status}")
    ctx.require(isinstance(payload.get("teacher_prompt"), str), "配置缺少 teacher_prompt")
    ctx.config_before = payload
    class_id = ctx.teacher["class_id"]

    rows = ctx.query_db("SELECT teacher_prompt FROM tutoring_settings WHERE class_id=?", (class_id,))
    ctx.require(len(rows) == 1 and rows[0]["teacher_prompt"] == payload["teacher_prompt"],
                "响应提示词与班级数据库配置不一致")
    db_skills = ctx.query_db(
        "SELECT id,name,kind,instructions,enabled FROM tutoring_skills WHERE class_id=? ORDER BY id",
        (class_id,),
    )
    api_skills = payload.get("skills")
    ctx.require(isinstance(api_skills, list), "配置响应 skills 应为数组")
    normalized = [{key: item[key] for key in ("id", "name", "kind", "instructions", "enabled")}
                  for item in api_skills]
    ctx.require(normalized == db_skills, "技能响应内容与班级数据库记录不一致")
    ctx.require(any(skill["kind"] == "solution_guidance" and skill["enabled"] for skill in db_skills),
                "数据库中至少应存在一项启用的解题引导技能")
