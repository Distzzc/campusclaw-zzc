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
