"""POST /api/auth/login: verify token response and persisted token/user records."""
import hashlib


def run(ctx):
    status, payload, _ = ctx.request("POST", "/api/auth/login", body={
        "username": ctx.username, "password": ctx.password,
    })
    ctx.require(status == 200, f"登录应返回 200，实际为 {status}")
    ctx.require(isinstance(payload, dict), "登录响应应为 JSON 对象")
    token = payload.get("access_token")
    user = payload.get("user")
    ctx.require(isinstance(token, str) and len(token) > 20, "登录响应缺少有效 access_token")
    ctx.require(payload.get("token_type") == "Bearer", "token_type 应为 Bearer")
    ctx.require(isinstance(user, dict), "登录响应缺少用户对象")
    ctx.require(user.get("username") == ctx.username, "响应用户名与请求账号不一致")
    ctx.require(user.get("role") == "student", "默认 API 测试账号应为学生")
    ctx.require("password" not in payload and "password_hash" not in payload, "响应不应泄露密码字段")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    rows = ctx.query_db(
        "SELECT access_tokens.user_id,access_tokens.token_hash,access_tokens.revoked_at,"
        "users.username,users.role,users.class_id FROM access_tokens "
        "JOIN users ON users.id=access_tokens.user_id WHERE access_tokens.token_hash=?",
        (token_hash,),
    )
    ctx.require(len(rows) == 1, "数据库中应存在该登录令牌的哈希记录")
    row = rows[0]
    ctx.require(row["username"] == user["username"], "数据库令牌用户与响应用户不一致")
    ctx.require(row["role"] == user["role"] and row["class_id"] == user["class_id"],
                "数据库角色/班级与登录响应不一致")
    ctx.require(row["revoked_at"] is None, "新登录令牌不应已撤销")
    ctx.require(row["token_hash"] != token, "数据库不应保存明文访问令牌")
    ctx.token, ctx.user = token, user

    teacher_status, teacher_payload, _ = ctx.request("POST", "/api/auth/login", body={
        "username": ctx.teacher_username, "password": ctx.teacher_password,
    })
    ctx.require(teacher_status == 200 and isinstance(teacher_payload, dict), "教师测试账号登录失败")
    teacher = teacher_payload.get("user", {})
    ctx.require(teacher.get("role") == "teacher", "教师登录响应角色不正确")
    ctx.teacher_token, ctx.teacher = teacher_payload["access_token"], teacher
