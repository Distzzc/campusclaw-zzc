import io
import os
import sqlite3
import hashlib

os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest

from app import create_app, init_db, seed_data


@pytest.fixture()
def client(tmp_path):
    database = tmp_path / "test.db"
    storage = tmp_path / "materials"
    app = create_app({"TESTING": True, "DATABASE": str(database), "STORAGE_PATH": str(storage)})
    with app.app_context():
        seed_data()
    return app.test_client()


def login(client, username, password):
    client.environ_base.pop("HTTP_AUTHORIZATION", None)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    if response.status_code == 200:
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {response.json['access_token']}"
    return response


def logout(client):
    response = client.post("/api/auth/logout")
    client.environ_base.pop("HTTP_AUTHORIZATION", None)
    return response


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}


def test_pages_load_templates_and_assets(client):
    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert b"class=\"auth-layout\"" in login_page.data
    assert b"/static/css/app.css" in login_page.data
    assert b"/static/js/app.js" in login_page.data
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200


def test_dashboard_differs_by_role(client):
    teacher_login = login(client, "a_teacher", "a-teacher-password")
    assert teacher_login.status_code == 200
    teacher_page = client.get("/materials")
    assert teacher_page.status_code == 200
    assert b"data-upload-form" in teacher_page.data
    assert b"a_teacher" not in teacher_page.data
    assert b"A\xe7\x8f\xad" not in teacher_page.data

    logout(client)
    login(client, "a_student", "a-student-password")
    student_page = client.get("/materials")
    assert student_page.status_code == 200
    assert b"data-teacher-only" in student_page.data
    assert "只读课堂空间".encode() in student_page.data


def test_material_page_includes_download_client_without_server_paths(client):
    page = client.get("/materials")
    assert page.status_code == 200
    assert b"a_teacher" not in page.data
    script = client.get("/static/js/app.js")
    assert b"data-download-id" in script.data
    assert b"/api/materials/" in script.data
    assert b"Authorization" in script.data
    assert b"storage_path" not in script.data


def test_login_returns_role_and_class(client):
    response = login(client, "a_teacher", "a-teacher-password")
    assert response.status_code == 200
    assert response.json["token_type"] == "Bearer"
    assert response.json["expires_in"] == 3600
    assert "password" not in response.json
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json["user"]["role"] == "teacher"
    assert me.json["user"]["class_id"]


def test_invalid_login_does_not_authenticate(client):
    response = login(client, "a_teacher", "wrong")
    assert response.status_code == 401
    assert client.get("/api/me").status_code == 401


def test_dashboard_shell_is_public_and_api_returns_401(client):
    assert client.get("/materials").status_code == 200
    assert client.get("/api/materials").status_code == 401


def test_student_upload_is_forbidden(client):
    login(client, "a_student", "a-student-password")
    response = client.post(
        "/api/materials",
        data={"title": "不能上传", "file": (io.BytesIO(b"x"), "x.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 403


def test_teacher_upload_appears_only_in_own_class(client):
    login(client, "a_teacher", "a-teacher-password")
    response = client.post(
        "/api/materials",
        data={"title": "A班材料", "file": (io.BytesIO(b"content"), "lesson.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    material_id = response.json["material"]["id"]
    assert client.get("/api/materials").json["materials"][0]["title"] == "A班材料"

    logout(client)
    login(client, "b_student", "b-student-password")
    assert client.get("/api/materials").json["materials"] == []
    assert client.get(f"/api/materials/{material_id}").status_code == 403


def test_password_is_hashed_and_seed_is_idempotent(client, tmp_path):
    with client.application.app_context():
        seed_data()
        connection = sqlite3.connect(client.application.config["DATABASE"])
        stored = connection.execute("SELECT password_hash FROM users WHERE username = 'a_teacher'").fetchone()[0]
        connection.close()
    assert stored != "a-teacher-password"
    assert stored.startswith("$argon2")


def test_token_is_only_stored_as_hash_and_schema_init_is_idempotent(client):
    token_response = login(client, "a_teacher", "a-teacher-password")
    token = token_response.json["access_token"]
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with client.application.app_context():
        init_db()
        init_db()
        row = sqlite3.connect(client.application.config["DATABASE"]).execute(
            "SELECT token_hash, expires_at, revoked_at FROM access_tokens"
        ).fetchone()
    assert row[0] == token_hash
    assert row[0] != token
    assert row[1] > 0
    assert row[2] is None


def test_missing_malformed_and_unknown_bearer_tokens_are_rejected(client):
    assert client.get("/api/me").status_code == 401
    for authorization in ("Bearer", "Basic abc", "Bearer invalid-token"):
        response = client.get("/api/me", headers={"Authorization": authorization})
        assert response.status_code == 401


def test_cookie_session_does_not_authenticate_api(client):
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 1
    assert client.get("/api/me").status_code == 401


def test_expired_token_is_rejected(client):
    response = login(client, "a_teacher", "a-teacher-password")
    token = response.json["access_token"]
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with client.application.app_context():
        database = sqlite3.connect(client.application.config["DATABASE"])
        database.execute("UPDATE access_tokens SET expires_at = 1 WHERE token_hash = ?", (token_hash,))
        database.commit()
        database.close()
    assert client.get("/api/me").status_code == 401


def test_logout_immediately_revokes_token(client):
    response = login(client, "a_teacher", "a-teacher-password")
    token = response.json["access_token"]
    assert logout(client).status_code == 200
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    assert client.get("/api/me").status_code == 401


def test_invalid_credentials_have_same_response(client):
    unknown_user = client.post("/api/auth/login", json={"username": "missing", "password": "x"})
    wrong_password = client.post("/api/auth/login", json={"username": "a_teacher", "password": "wrong"})
    assert unknown_user.status_code == wrong_password.status_code == 401
    assert unknown_user.json == wrong_password.json == {"error": "账号或密码错误"}


def create_material(client, title="下载材料", filename="lesson.txt", content=b"lesson content"):
    response = client.post(
        "/api/materials",
        data={"title": title, "file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    return response.json["material"]["id"]


def test_download_requires_authentication(client):
    assert client.get("/api/materials/1/download").status_code == 401


def test_teacher_and_student_can_download_same_class_material(client):
    login(client, "a_teacher", "a-teacher-password")
    material_id = create_material(client, filename="课程材料.md", content="课程内容".encode())

    response = client.get(f"/api/materials/{material_id}/download")
    assert response.status_code == 200
    assert response.data == "课程内容".encode()
    assert "filename*=UTF-8''%E8%AF%BE%E7%A8%8B%E6%9D%90%E6%96%99.md" in response.headers["Content-Disposition"]
    assert response.content_type.startswith("text/markdown")

    logout(client)
    login(client, "a_student", "a-student-password")
    response = client.get(f"/api/materials/{material_id}/download")
    assert response.status_code == 200
    assert response.data == "课程内容".encode()


def test_cross_class_download_is_forbidden(client):
    login(client, "a_teacher", "a-teacher-password")
    material_id = create_material(client)
    logout(client)
    login(client, "b_student", "b-student-password")
    response = client.get(f"/api/materials/{material_id}/download?class_id=1&storage_path=/etc/passwd")
    assert response.status_code == 403
    assert b"lesson content" not in response.data
    assert b"storage" not in response.data.lower()


def test_non_indexed_and_missing_files_cannot_download(client, tmp_path):
    login(client, "a_teacher", "a-teacher-password")
    material_id = create_material(client)
    with client.application.app_context():
        database = sqlite3.connect(client.application.config["DATABASE"])
        database.execute("UPDATE materials SET index_status = 'pending' WHERE id = ?", (material_id,))
        database.commit()
        database.close()
    assert client.get(f"/api/materials/{material_id}/download").status_code == 403

    with client.application.app_context():
        database = sqlite3.connect(client.application.config["DATABASE"])
        missing_inside_storage = tmp_path / "materials" / "gone.txt"
        database.execute("UPDATE materials SET index_status = 'indexed', storage_path = ? WHERE id = ?", (str(missing_inside_storage), material_id))
        database.commit()
        database.close()
    response = client.get(f"/api/materials/{material_id}/download")
    assert response.status_code == 404
    assert b"storage_path" not in response.data


def test_download_rejects_paths_outside_storage_root(client, tmp_path):
    login(client, "a_teacher", "a-teacher-password")
    material_id = create_material(client)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with client.application.app_context():
        database = sqlite3.connect(client.application.config["DATABASE"])
        database.execute("UPDATE materials SET storage_path = ? WHERE id = ?", (str(outside), material_id))
        database.commit()
        database.close()
    response = client.get(f"/api/materials/{material_id}/download")
    assert response.status_code == 403
    assert b"secret" not in response.data


def test_download_rejects_symlink_outside_storage_root(client, tmp_path):
    login(client, "a_teacher", "a-teacher-password")
    material_id = create_material(client)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "materials" / "linked.txt"
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(outside)
    with client.application.app_context():
        database = sqlite3.connect(client.application.config["DATABASE"])
        database.execute("UPDATE materials SET storage_path = ? WHERE id = ?", (str(link), material_id))
        database.commit()
        database.close()
    response = client.get(f"/api/materials/{material_id}/download")
    assert response.status_code == 403
    assert b"secret" not in response.data
