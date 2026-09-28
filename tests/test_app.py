import io
import os
import sqlite3

os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest

from app import create_app, seed_data


@pytest.fixture()
def client(tmp_path):
    database = tmp_path / "test.db"
    storage = tmp_path / "materials"
    app = create_app({"TESTING": True, "DATABASE": str(database), "STORAGE_PATH": str(storage)})
    with app.app_context():
        seed_data()
    return app.test_client()


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password})


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
    login(client, "a_teacher", "a-teacher-password")
    teacher_page = client.get("/materials")
    assert teacher_page.status_code == 200
    assert b"data-upload-form" in teacher_page.data

    client.post("/logout")
    login(client, "a_student", "a-student-password")
    student_page = client.get("/materials")
    assert student_page.status_code == 200
    assert b"data-upload-form" not in student_page.data
    assert "只读课堂空间".encode() in student_page.data


def test_material_page_includes_download_client_without_server_paths(client):
    page = client.get("/materials")
    assert page.status_code == 302
    script = client.get("/static/js/app.js")
    assert b"data-download-id" in script.data
    assert b"/api/materials/" in script.data
    assert b"storage_path" not in script.data


def test_login_returns_role_and_class(client):
    response = login(client, "a_teacher", "a-teacher-password")
    assert response.status_code == 302
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json["user"]["role"] == "teacher"
    assert me.json["user"]["class_id"]


def test_invalid_login_does_not_authenticate(client):
    response = login(client, "a_teacher", "wrong")
    assert response.status_code == 200
    assert client.get("/api/me").status_code == 401


def test_protected_page_redirects_and_api_returns_401(client):
    assert client.get("/materials").status_code == 302
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

    client.post("/logout")
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

    client.post("/logout")
    login(client, "a_student", "a-student-password")
    response = client.get(f"/api/materials/{material_id}/download")
    assert response.status_code == 200
    assert response.data == "课程内容".encode()


def test_cross_class_download_is_forbidden(client):
    login(client, "a_teacher", "a-teacher-password")
    material_id = create_material(client)
    client.post("/logout")
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
