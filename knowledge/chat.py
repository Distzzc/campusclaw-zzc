"""Persistent class coaching configuration and private student sessions."""

DEFAULT_GUIDANCE_NAME = "解题引导"
DEFAULT_GUIDANCE = (
    "通过解释关键概念、拆解步骤和检查理解帮助学生学习；根据问题给出完整回答和最终结论。"
)
LEGACY_DEFAULT_GUIDANCE = (
    "通过澄清问题、逐步提示和拆解关键步骤帮助学生自己推理；先询问学生已有思路，"
    "每次只给适量提示并检查理解。不得代写或直接揭示题目的最终答案。"
)
MAX_TEACHER_PROMPT = 4000
MAX_SKILL_NAME = 80
MAX_SKILL_INSTRUCTIONS = 2000
SKILL_KINDS = {"solution_guidance", "other"}


def ensure_chat_schema(db):
    """Create chat tables and initialize defaults without disturbing existing data."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tutoring_settings (
            class_id INTEGER PRIMARY KEY REFERENCES classes(id),
            teacher_prompt TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tutoring_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES classes(id),
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            instructions TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (class_id, name)
        );
        CREATE INDEX IF NOT EXISTS tutoring_skills_scope
            ON tutoring_skills(class_id, enabled, kind);
        CREATE TABLE IF NOT EXISTS tutoring_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id),
            class_id INTEGER NOT NULL REFERENCES classes(id),
            title TEXT NOT NULL DEFAULT '新会话',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS tutoring_sessions_owner
            ON tutoring_sessions(owner_id, class_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS tutoring_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES tutoring_sessions(id),
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed')),
            citations_json TEXT NOT NULL DEFAULT '[]',
            error_code TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS tutoring_messages_session
            ON tutoring_messages(session_id, id);
        DROP TRIGGER IF EXISTS tutoring_class_defaults;
        CREATE TRIGGER tutoring_class_defaults
        AFTER INSERT ON classes
        BEGIN
            INSERT OR IGNORE INTO tutoring_settings(class_id) VALUES (NEW.id);
            INSERT OR IGNORE INTO tutoring_skills
                (class_id, name, kind, instructions, enabled)
                VALUES (NEW.id, '解题引导', 'solution_guidance',
                    '通过解释关键概念、拆解步骤和检查理解帮助学生学习；根据问题给出完整回答和最终结论。', 1);
        END;
    """)
    seed_chat_defaults(db)
    db.commit()


def seed_chat_defaults(db):
    """Idempotently provision settings and a required guidance skill per class."""
    for row in db.execute("SELECT id FROM classes").fetchall():
        class_id = row[0]
        db.execute("INSERT OR IGNORE INTO tutoring_settings(class_id) VALUES (?)", (class_id,))
        db.execute(
            "UPDATE tutoring_skills SET instructions=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE class_id=? AND name=? AND kind='solution_guidance' AND instructions=?",
            (DEFAULT_GUIDANCE, class_id, DEFAULT_GUIDANCE_NAME, LEGACY_DEFAULT_GUIDANCE),
        )
        # Existing custom skills/configuration are preserved; only seed the required
        # built-in skill when the class has no enabled solution-guidance skill.
        enabled = db.execute(
            "SELECT 1 FROM tutoring_skills WHERE class_id=? AND kind='solution_guidance' AND enabled=1 LIMIT 1",
            (class_id,),
        ).fetchone()
        if enabled is None:
            db.execute(
                "INSERT OR IGNORE INTO tutoring_skills(class_id,name,kind,instructions,enabled) VALUES (?,?,?,?,1)",
                (class_id, DEFAULT_GUIDANCE_NAME, "solution_guidance", DEFAULT_GUIDANCE),
            )
            existing = db.execute(
                "SELECT id FROM tutoring_skills WHERE class_id=? AND kind='solution_guidance' AND enabled=0 LIMIT 1",
                (class_id,),
            ).fetchone()
            if existing:
                db.execute("UPDATE tutoring_skills SET enabled=1 WHERE id=?", (existing[0],))


def load_class_config(db, class_id):
    settings = db.execute(
        "SELECT teacher_prompt, updated_at FROM tutoring_settings WHERE class_id=?", (class_id,)
    ).fetchone()
    if settings is None:
        return None
    skills = db.execute(
        "SELECT id,name,kind,instructions,enabled,updated_at FROM tutoring_skills "
        "WHERE class_id=? ORDER BY id", (class_id,)
    ).fetchall()
    return {
        "teacher_prompt": settings["teacher_prompt"],
        "updated_at": settings["updated_at"],
        "skills": [dict(row) for row in skills],
    }


def active_guidance(db, class_id):
    row = db.execute(
        "SELECT s.teacher_prompt,k.instructions FROM tutoring_settings s "
        "JOIN tutoring_skills k ON k.class_id=s.class_id "
        "WHERE s.class_id=? AND k.kind='solution_guidance' AND k.enabled=1 ORDER BY k.id LIMIT 1",
        (class_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("班级缺少启用的解题引导技能")
    return {"teacher_prompt": row["teacher_prompt"], "skill": row["instructions"]}


def update_teacher_prompt(db, class_id, prompt):
    if not isinstance(prompt, str) or len(prompt) > MAX_TEACHER_PROMPT:
        raise ValueError("提示词长度不能超过 4000 个字符")
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        "UPDATE tutoring_settings SET teacher_prompt=?,updated_at=CURRENT_TIMESTAMP WHERE class_id=?",
        (prompt.strip(), class_id),
    )
    db.commit()
    return load_class_config(db, class_id)


def save_skill(db, class_id, name, kind, instructions, enabled=True, skill_id=None):
    if (not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_SKILL_NAME
            or not isinstance(kind, str) or kind not in SKILL_KINDS
            or not isinstance(instructions, str) or not instructions.strip()
            or len(instructions) > MAX_SKILL_INSTRUCTIONS or type(enabled) is not bool):
        raise ValueError("技能名称、类型、指导内容或启用状态无效")
    name, instructions = name.strip(), instructions.strip()
    db.execute("BEGIN IMMEDIATE")
    try:
        if skill_id is None:
            cursor = db.execute(
                "INSERT INTO tutoring_skills(class_id,name,kind,instructions,enabled) VALUES (?,?,?,?,?)",
                (class_id, name, kind, instructions, int(enabled)),
            )
            skill_id = cursor.lastrowid
        else:
            cursor = db.execute(
                "UPDATE tutoring_skills SET name=?,kind=?,instructions=?,enabled=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND class_id=?",
                (name, kind, instructions, int(enabled), skill_id, class_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("技能不存在")
        _require_enabled_guidance(db, class_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return db.execute(
        "SELECT id,name,kind,instructions,enabled,updated_at FROM tutoring_skills WHERE id=? AND class_id=?",
        (skill_id, class_id),
    ).fetchone()


def delete_skill(db, class_id, skill_id):
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT kind FROM tutoring_skills WHERE id=? AND class_id=?", (skill_id, class_id)
        ).fetchone()
        if row is None:
            raise LookupError("技能不存在")
        db.execute("DELETE FROM tutoring_skills WHERE id=? AND class_id=?", (skill_id, class_id))
        _require_enabled_guidance(db, class_id)
        db.commit()
    except Exception:
        db.rollback()
        raise


def _require_enabled_guidance(db, class_id):
    row = db.execute(
        "SELECT 1 FROM tutoring_skills WHERE class_id=? AND kind='solution_guidance' AND enabled=1 LIMIT 1",
        (class_id,),
    ).fetchone()
    if row is None:
        raise ValueError("每个班级必须至少保留一项启用的解题引导技能")
