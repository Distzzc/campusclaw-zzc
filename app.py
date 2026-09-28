import os
import sqlite3
import mimetypes
from functools import wraps
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "data" / "app.db"
DEFAULT_STORAGE = BASE_DIR / "data" / "materials"
PASSWORD_HASHER = PasswordHasher()
mimetypes.add_type("text/markdown", ".md")

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

    @app.get("/")
    def home():
        return redirect(url_for("login"))

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
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/materials")
    @login_required(page=True)
    def materials_page():
        return render_template("dashboard.html", user=g.user)

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

    @app.get("/api/materials/<int:material_id>/download")
    @login_required()
    def download_material(material_id):
        material = get_db().execute(
            "SELECT * FROM materials WHERE id = ? AND class_id = ? AND index_status = 'indexed'",
            (material_id, g.user["class_id"]),
        ).fetchone()
        if material is None:
            abort(403)

        storage_root = Path(current_app().config["STORAGE_PATH"]).resolve()
        target = Path(material["storage_path"]).resolve(strict=False)
        try:
            target.relative_to(storage_root)
        except ValueError:
            abort(403)
        if not target.is_file():
            abort(404)

        download_name = Path(material["filename"] or "material.bin").name
        mimetype = mimetypes.guess_type(download_name)[0] or "application/octet-stream"
        return send_file(target, as_attachment=True, download_name=download_name, mimetype=mimetype)

    @app.errorhandler(400)
    def bad_request(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="请求参数无效"), 400
        return render_template("error.html", code=400, message="请求参数无效，请检查后重试。"), 400

    @app.errorhandler(403)
    def forbidden(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="forbidden"), 403
        return render_template("error.html", code=403, message="你没有权限访问这个内容。"), 403

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="not found"), 404
        return render_template("error.html", code=404, message="页面不存在或已经移动。"), 404

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
