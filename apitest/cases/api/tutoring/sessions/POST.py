"""POST /api/tutoring/sessions: compare created session with SQLite ownership."""


def run(ctx):
    token = ctx.require_student()
    title = "API 验收会话"
    status, payload, _ = ctx.request("POST", "/api/tutoring/sessions", token=token, body={"title": title})
    ctx.require(status == 201 and isinstance(payload, dict), f"创建会话应返回 201，实际为 {status}")
    session = payload.get("session", {})
    ctx.require(isinstance(session.get("id"), int) and session.get("title") == title,
                "创建会话响应 ID/标题不正确")
    rows = ctx.query_db(
        "SELECT id,owner_id,class_id,title FROM tutoring_sessions WHERE id=?",
        (session["id"],),
    )
    ctx.require(rows == [{"id": session["id"], "owner_id": ctx.user["id"],
                         "class_id": ctx.user["class_id"], "title": title}],
                "会话创建响应与数据库所有者/班级/标题不一致")
    listed_status, listed, _ = ctx.request("GET", "/api/tutoring/sessions", token=token)
    ctx.require(listed_status == 200 and any(item.get("id") == session["id"]
                                               for item in listed.get("sessions", [])),
                "会话列表未包含刚创建的会话")
    ctx.session = session
