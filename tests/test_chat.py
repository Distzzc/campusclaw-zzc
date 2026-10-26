import sqlite3
import os
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("SECRET_KEY", "chat-schema-test-secret-key-at-least-32-bytes")
from app import create_app, get_db, init_db, seed_data


def make_app(path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "chat-schema-test-secret-key-at-least-32-bytes",
        "DATABASE": str(path),
        "STORAGE_PATH": str(path.parent / "materials"),
    })


def test_chat_schema_migrates_old_database_and_seeds_defaults(tmp_path):
    database = tmp_path / "old.db"
    raw = sqlite3.connect(database)
    raw.executescript("""
        CREATE TABLE classes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL, class_id INTEGER NOT NULL);
        CREATE TABLE materials (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          filename TEXT NOT NULL, class_id INTEGER NOT NULL, created_by INTEGER NOT NULL,
          storage_path TEXT NOT NULL, index_status TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO classes(name) VALUES ('旧班');
        INSERT INTO materials(title,filename,class_id,created_by,storage_path,index_status)
          VALUES ('保留材料','old.txt',1,1,'/old/path','indexed');
    """)
    raw.commit()
    raw.close()

    app = make_app(database)
    with app.app_context():
        init_db()
        init_db()
        db = get_db()
        assert db.execute("SELECT title FROM materials WHERE id=1").fetchone()[0] == "保留材料"
        assert db.execute("SELECT COUNT(*) FROM tutoring_settings").fetchone()[0] == 1
        skills = db.execute("SELECT name,kind,enabled FROM tutoring_skills WHERE class_id=1").fetchall()
        assert [(row[0], row[1], row[2]) for row in skills] == [("解题引导", "solution_guidance", 1)]
        db.execute("UPDATE tutoring_settings SET teacher_prompt='已有教师配置' WHERE class_id=1")
        seed_data()
        init_db()
        assert db.execute("SELECT teacher_prompt FROM tutoring_settings WHERE class_id=1").fetchone()[0] == "已有教师配置"
        assert db.execute("SELECT COUNT(*) FROM tutoring_skills WHERE class_id=1").fetchone()[0] == 1


def test_new_classes_receive_guidance_skill_once(tmp_path):
    app = make_app(tmp_path / "new.db")
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO classes(name) VALUES ('新班')")
        db.commit()
        class_id = db.execute("SELECT id FROM classes WHERE name='新班'").fetchone()[0]
        assert db.execute(
            "SELECT COUNT(*) FROM tutoring_skills WHERE class_id=? AND kind='solution_guidance' AND enabled=1",
            (class_id,),
        ).fetchone()[0] == 1
        init_db()
        assert db.execute("SELECT COUNT(*) FROM tutoring_skills WHERE class_id=?", (class_id,)).fetchone()[0] == 1


def login(client, username):
    password = username.replace("_", "-") + "-password"
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + response.json["access_token"]


def test_teacher_tutoring_config_is_class_scoped_and_students_cannot_access(tmp_path):
    app = make_app(tmp_path / "config.db")
    with app.app_context():
        seed_data()
    client = app.test_client()
    login(client, "a_teacher")
    initial = client.get("/api/tutoring/config")
    assert initial.status_code == 200
    assert any(skill["kind"] == "solution_guidance" and skill["enabled"] for skill in initial.json["skills"])
    assert client.put("/api/tutoring/config", json={
        "teacher_prompt": "先询问学生思路", "class_id": 2,
    }).status_code == 400
    assert client.put("/api/tutoring/config", json={"teacher_prompt": "先询问学生思路"}).status_code == 200

    login(client, "b_teacher")
    other_class = client.get("/api/tutoring/config").json
    assert other_class["teacher_prompt"] == ""
    assert other_class["skills"][0]["instructions"] != "先询问学生思路"
    login(client, "a_student")
    assert client.get("/api/tutoring/config").status_code == 403
    assert client.get("/api/tutoring/skills").status_code == 403
    assert client.put("/api/tutoring/config", json={"teacher_prompt": "x"}).status_code == 403


def test_guidance_skill_last_enabled_item_cannot_be_disabled_or_deleted(tmp_path):
    app = make_app(tmp_path / "skills.db")
    with app.app_context():
        seed_data()
    client = app.test_client()
    login(client, "a_teacher")
    skills = client.get("/api/tutoring/skills").json["skills"]
    required = next(skill for skill in skills if skill["kind"] == "solution_guidance")
    payload = {key: required[key] for key in ("name", "kind", "instructions")}
    payload["enabled"] = False
    failed_update = client.put(f"/api/tutoring/skills/{required['id']}", json=payload)
    assert failed_update.status_code == 400
    assert client.delete(f"/api/tutoring/skills/{required['id']}").status_code == 409
    assert client.get("/api/tutoring/skills").json["skills"][0]["enabled"] == 1

    created = client.post("/api/tutoring/skills", json={
        "name": "追问", "kind": "solution_guidance", "instructions": "询问学生下一步依据",
    })
    assert created.status_code == 201
    assert client.delete(f"/api/tutoring/skills/{required['id']}").status_code == 200
    assert client.delete(f"/api/tutoring/skills/{created.json['skill']['id']}").status_code == 409
    assert client.post("/api/tutoring/skills", json={
        "name": "太长", "kind": "other", "instructions": "x" * 2001,
    }).status_code == 400


def test_concurrent_skill_updates_preserve_one_enabled_guidance_skill(tmp_path):
    from knowledge.chat import save_skill

    database = tmp_path / "concurrent.db"
    app = make_app(database)
    with app.app_context():
        seed_data()
        first = get_db().execute(
            "SELECT id,name,kind,instructions FROM tutoring_skills WHERE class_id=1 AND kind='solution_guidance'"
        ).fetchone()
        second = save_skill(get_db(), 1, "备用引导", "solution_guidance", "询问学生思路", True)
        get_db().commit()
    skill_ids = [first["id"], second["id"]]

    def disable(skill_id):
        connection = sqlite3.connect(database, timeout=5)
        try:
            row = connection.execute("SELECT name,kind,instructions FROM tutoring_skills WHERE id=?", (skill_id,)).fetchone()
            try:
                save_skill(connection, 1, row[0], row[1], row[2], False, skill_id)
                return "disabled"
            except ValueError:
                return "protected"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(disable, skill_ids))
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM tutoring_skills WHERE class_id=1 AND kind='solution_guidance' AND enabled=1"
        ).fetchone()[0]
    assert sorted(outcomes) == ["disabled", "protected"]
    assert count == 1


def test_student_sessions_are_private_persistent_and_deletable(tmp_path):
    database = tmp_path / "sessions.db"
    app = make_app(database)
    with app.app_context():
        seed_data()
    client = app.test_client()
    login(client, "a_student")
    created = client.post("/api/tutoring/sessions", json={"title": "代数练习", "owner_id": 4, "class_id": 2})
    assert created.status_code == 400
    created = client.post("/api/tutoring/sessions", json={"title": "代数练习"})
    assert created.status_code == 201
    session_id = created.json["session"]["id"]
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO tutoring_messages(session_id,role,content,status,citations_json) VALUES (?,?,?,?,?)",
            (session_id, "user", "怎样解方程？", "completed", "[]"),
        )
        db.execute("INSERT INTO tutoring_messages(session_id,role,content,status) VALUES (?,?,?,?)",
                   (session_id, "assistant", "先检查等式两边。", "completed"))
        db.commit()
    assert client.get("/api/tutoring/sessions").json["sessions"][0]["id"] == session_id
    assert len(client.get(f"/api/tutoring/sessions/{session_id}").json["messages"]) == 2

    # Reopening the app against the same SQLite file preserves the private history.
    reopened = make_app(database).test_client()
    login(reopened, "a_student")
    assert reopened.get(f"/api/tutoring/sessions/{session_id}").json["messages"][0]["content"] == "怎样解方程？"
    login(reopened, "b_student")
    assert reopened.get(f"/api/tutoring/sessions/{session_id}").status_code == 404
    assert reopened.delete(f"/api/tutoring/sessions/{session_id}").status_code == 404
    login(reopened, "a_teacher")
    assert reopened.get(f"/api/tutoring/sessions/{session_id}").status_code == 403
    login(client, "a_student")
    assert client.delete(f"/api/tutoring/sessions/{session_id}").status_code == 200
    assert client.get(f"/api/tutoring/sessions/{session_id}").status_code == 404


def test_tutoring_feature_flag_hides_ui_and_disables_api(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "chat-schema-test-secret-key-at-least-32-bytes",
        "DATABASE": str(tmp_path / "disabled.db"),
        "STORAGE_PATH": str(tmp_path / "materials"),
        "TUTORING_ENABLED": False,
    })
    with app.app_context():
        seed_data()
    client = app.test_client()
    login(client, "a_teacher")
    page = client.get("/materials")
    assert b"data-tutoring-teacher" not in page.data
    assert client.get("/api/tutoring/config").status_code == 404
    login(client, "a_student")
    assert client.get("/api/tutoring/sessions").status_code == 404