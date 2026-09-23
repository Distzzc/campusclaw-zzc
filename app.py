import os
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import Flask, abort, g, jsonify, redirect, render_template_string, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "data" / "app.db"
DEFAULT_STORAGE = BASE_DIR / "data" / "materials"
PASSWORD_HASHER = PasswordHasher()

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('teacher', 'student')),
    class_id INTEGER NOT NULL REFERENCES classes(id)
);
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    created_by INTEGER NOT NULL REFERENCES users(id),
    storage_path TEXT NOT NULL,
    index_status TEXT NOT NULL CHECK (index_status IN ('pending', 'indexed', 'failed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

LOGIN_PAGE = """<!doctype html>
<title>登录</title>
<h1>登录</h1>
<form method="post">
  <label>账号 <input name="username" required></label>
  <label>密码 <input name="password" type="password" required></label>
  <button type="submit">登录</button>
</form>
{% if error %}<p>{{ error }}</p>{% endif %}
"""

MATERIALS_PAGE = """<!doctype html>
<title>班级材料</title>
<h1>班级材料</h1>
<p>当前用户：{{ user.username }}（{{ user.role }}，{{ user.class_name }}）</p>
{% if user.role == 'teacher' %}
<form method="post" action="{{ url_for('create_material') }}" enctype="multipart/form-data">
  <input name="title" placeholder="材料标题" required>
  <input name="file" type="file" required>
  <button type="submit">上传</button>
</form>
{% endif %}
<ul>{% for material in materials %}<li>{{ material.title }} - {{ material.filename }}</li>{% endfor %}</ul>
<form method="post" action="{{ url_for('logout') }}"><button>退出</button></form>
"""


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        DATABASE=os.environ.get("DATABASE_PATH", str(DEFAULT_DATABASE)),
        STORAGE_PATH=os.environ.get("STORAGE_PATH", str(DEFAULT_STORAGE)),
        TESTING=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=3600,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        if app.config.get("TESTING"):
            app.config["SECRET_KEY"] = "test-only-secret"
        else:
            raise RuntimeError("SECRET_KEY must be configured on the server")

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["STORAGE_PATH"]).mkdir(parents=True, exist_ok=True)

    @app.before_request
    def load_user():
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            g.user = get_db().execute(
                "SELECT users.*, classes.name AS class_name FROM users "
                "JOIN classes ON classes.id = users.class_id WHERE users.id = ?",
                (user_id,),
            ).fetchone()

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.cli.command("seed-data")
    def seed_data_command():
        """Load idempotent development/test accounts and classes."""
        seed_data()
        print("Seed data loaded")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.route("/login", methods=("GET", "POST"))
    def login():
        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user is None:
                error = "账号或密码错误"
            else:
                try:
                    PASSWORD_HASHER.verify(user["password_hash"], password)
                except VerifyMismatchError:
                    error = "账号或密码错误"
                else:
                    session.clear()
                    session["user_id"] = user["id"]
                    return redirect(url_for("materials_page"))
        return render_template_string(LOGIN_PAGE, error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/materials")
    @login_required(page=True)
    def materials_page():
        materials = list_materials()
        return render_template_string(MATERIALS_PAGE, user=g.user, materials=materials)

    @app.get("/api/me")
    @login_required()
    def current_user():
        return jsonify(user=user_payload(g.user))

    @app.get("/api/materials")
    @login_required()
    def materials_api():
        return jsonify(materials=[material_payload(row) for row in list_materials()])

    @app.post("/api/materials")
    @login_required(role="teacher")
    def create_material_api():
        return create_material(as_json=True)

    @app.post("/materials")
    @login_required(role="teacher", page=True)
    def create_material(as_json=False):
        file = request.files.get("file")
        title = request.form.get("title", "").strip()
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            title = str(payload.get("title", "")).strip()
        if not file or not title:
            if as_json or request.path.startswith("/api/"):
                return jsonify(error="title and file are required"), 400
            abort(400)

        filename = Path(file.filename or "material.bin").name
        db = get_db()
        cursor = db.execute(
            "INSERT INTO materials (title, filename, class_id, created_by, storage_path, index_status) "
            "VALUES (?, ?, ?, ?, '', 'pending')",
            (title, filename, g.user["class_id"], g.user["id"]),
        )
        material_id = cursor.lastrowid
        target = Path(current_app().config["STORAGE_PATH"]) / f"{material_id}-{filename}"
        try:
            file.save(target)
            db.execute(
                "UPDATE materials SET storage_path = ?, index_status = 'indexed' WHERE id = ?",
                (str(target), material_id),
            )
            db.commit()
        except Exception:
            db.execute("UPDATE materials SET index_status = 'failed' WHERE id = ?", (material_id,))
            db.commit()
            if as_json or request.path.startswith("/api/"):
                return jsonify(error="material storage failed"), 500
            abort(500)

        material = db.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        if as_json or request.path.startswith("/api/"):
            return jsonify(material=material_payload(material)), 201
        return redirect(url_for("materials_page"))

    @app.get("/api/materials/<int:material_id>")
    @login_required()
    def material_detail(material_id):
        material = get_db().execute(
            "SELECT * FROM materials WHERE id = ? AND class_id = ? AND index_status = 'indexed'",
            (material_id, g.user["class_id"]),
        ).fetchone()
        if material is None:
            abort(403)
        return jsonify(material=material_payload(material))

    with app.app_context():
        init_db()

    return app


def current_app():
    from flask import current_app as flask_current_app
    return flask_current_app


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app().config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def init_db():
    get_db().executescript(SCHEMA)
    get_db().commit()


def seed_data():
    app = current_app()
    if os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("seed data is disabled in production")
    db = get_db()
    for class_name in ("A班", "B班"):
        db.execute("INSERT OR IGNORE INTO classes (name) VALUES (?)", (class_name,))
    class_ids = {
        row["name"]: row["id"] for row in db.execute("SELECT id, name FROM classes WHERE name IN ('A班', 'B班')")
    }
    for class_name in ("A班", "B班"):
        prefix = "a" if class_name == "A班" else "b"
        for role in ("teacher", "student"):
            username = f"{prefix}_{role}"
            password = f"{prefix}-{role}-password"
            db.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, role, class_id) VALUES (?, ?, ?, ?)",
                (username, PASSWORD_HASHER.hash(password), role, class_ids[class_name]),
            )
    db.commit()


def list_materials():
    return get_db().execute(
        "SELECT materials.*, classes.name AS class_name FROM materials "
        "JOIN classes ON classes.id = materials.class_id "
        "WHERE materials.class_id = ? AND materials.index_status = 'indexed' "
        "ORDER BY materials.id DESC",
        (g.user["class_id"],),
    ).fetchall()


def material_payload(row):
    return {"id": row["id"], "title": row["title"], "filename": row["filename"], "class_id": row["class_id"]}


def user_payload(user):
    return {"id": user["id"], "username": user["username"], "role": user["role"], "class_id": user["class_id"]}


def login_required(role=None, page=False):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                if page:
                    return redirect(url_for("login"))
                return jsonify(error="authentication required"), 401
            if role and g.user["role"] != role:
                return jsonify(error="forbidden"), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
