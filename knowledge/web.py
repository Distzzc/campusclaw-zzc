"""Integration without changing the existing JWT or file authorization model."""
import os
import json
import sqlite3
import time
from functools import wraps
from pathlib import Path

import click
from flask import Response, g, jsonify, request, abort

from .adapters import LocalEmbedding, ChromaVectors, Unavailable
from .answers import (LocalAnswerer, RemoteAnswerer, answer, tutoring_answer,
                      InvalidAnswer, EvidenceChanged)
from .service import Knowledge, parameters, source, Busy
from .chat import (load_class_config, update_teacher_prompt, save_skill, delete_skill,
                   active_guidance)


def configure(app, get_db, login_required):
    data = Path(app.config['DATABASE']).parent
    defaults = {
        'MAX_CONTENT_LENGTH': 25 * 1024 * 1024,
        'INDEX_MAX_CHARACTERS': 2_000_000, 'INDEX_TIMEOUT': 120.0,
        'VECTOR_MIN_SIMILARITY': 0.35,
        'VECTOR_PATH': str(data / 'vector-index'),
        'EMBEDDING_MODEL_PATH': '', 'EMBEDDING_MODEL_REVISION': 'local-v1',
        'QA_MODEL_PATH': '', 'QA_CONTEXT_TOKENS': 4096, 'QA_MAX_NEW_TOKENS': 768,
        'QA_TIMEOUT': 120.0, 'QA_CONCURRENCY': 1, 'QA_THREADS': 4,
        'QA_PROVIDER': 'auto', 'QA_API_BASE_URL': '', 'QA_API_KEY': '',
        'QA_API_MODEL': '', 'QA_API_TIMEOUT': 45,
        'QA_LOCK_PATH': str(data / 'qa-locks'), 'KNOWLEDGE_ENABLED': True,
        'TUTORING_ENABLED': True,
        'TOOLS_ENABLED': True, 'TOOLS_CONFIG_PATH': '', 'TOOLS_TIMEOUT': 10,
        'MCP_SERVER_ENABLED': True,
    }
    for name, default in defaults.items():
        if name not in app.config or app.config[name] is None:
            value = os.environ.get(name)
            app.config[name] = default if value is None else (
                value.lower() not in ('0', 'false', 'no') if type(default) is bool else type(default)(value))

    def tutoring_enabled(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not app.config['TUTORING_ENABLED']:
                abort(404)
            return view(*args, **kwargs)
        return wrapped
    vectors = app.config.get('KNOWLEDGE_VECTORS') or ChromaVectors(app.config['VECTOR_PATH'])
    embedding = app.config.get('KNOWLEDGE_EMBEDDING') or LocalEmbedding(
        app.config['EMBEDDING_MODEL_PATH'], app.config['EMBEDDING_MODEL_REVISION'])
    knowledge = Knowledge(app.config, embedding, vectors)
    app.extensions['knowledge'] = knowledge
    if app.config.get('KNOWLEDGE_ANSWERER'):
        app.extensions['answerer'] = app.config['KNOWLEDGE_ANSWERER']
    elif app.config['QA_PROVIDER'] in ('api', 'auto') and app.config['QA_API_KEY'] and app.config['QA_API_BASE_URL']:
        app.extensions['answerer'] = RemoteAnswerer(app.config)
    else:
        app.extensions['answerer'] = LocalAnswerer(app.config)
    app.extensions['knowledge_fallback_answerer'] = (
        RemoteAnswerer(app.config)
        if app.config['QA_API_KEY'] and app.config['QA_API_BASE_URL'] and app.config['QA_API_MODEL']
        and not isinstance(app.extensions['answerer'], RemoteAnswerer)
        else None
    )

    from integrations.web import configure as configure_integrations
    configure_integrations(app, get_db, login_required)

    @app.errorhandler(Unavailable)
    def unavailable(error):
        return jsonify(error=str(error)), 503

    @app.errorhandler(InvalidAnswer)
    def invalid_answer(error):
        return jsonify(error=str(error)), 502

    @app.errorhandler(EvidenceChanged)
    def evidence_changed(error):
        return jsonify(error=str(error)), 409

    def inputs(question=False):
        if not app.config['KNOWLEDGE_ENABLED']:
            abort(404)
        try:
            return parameters(request.get_json(silent=True), question=question)
        except ValueError as error:
            abort(400, description=str(error))

    @app.post('/api/search')
    @login_required()
    def search():
        query, mode, limit = inputs()
        return jsonify(mode=mode, results=knowledge.search(get_db(), g.user['class_id'], query, mode, limit))

    @app.get('/api/knowledge/chunks/<chunk_id>')
    @login_required()
    def passage(chunk_id):
        if not app.config['KNOWLEDGE_ENABLED']:
            abort(404)
        row = knowledge.passage(get_db(), g.user['class_id'], chunk_id)
        if row is None:
            abort(404)
        return jsonify(source(row))

    @app.get('/api/tutoring/config')
    @tutoring_enabled
    @login_required(role='teacher')
    def tutoring_config():
        config = load_class_config(get_db(), g.user['class_id'])
        if config is None:
            abort(404)
        return jsonify(config)

    @app.put('/api/tutoring/config')
    @tutoring_enabled
    @login_required(role='teacher')
    def save_tutoring_config():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'teacher_prompt'}:
            return jsonify(error='请求参数无效'), 400
        try:
            return jsonify(update_teacher_prompt(get_db(), g.user['class_id'], payload['teacher_prompt']))
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.get('/api/tutoring/skills')
    @tutoring_enabled
    @login_required(role='teacher')
    def tutoring_skills():
        config = load_class_config(get_db(), g.user['class_id'])
        if config is None:
            abort(404)
        return jsonify(skills=config['skills'])

    @app.post('/api/tutoring/skills')
    @tutoring_enabled
    @login_required(role='teacher')
    def create_tutoring_skill():
        payload = request.get_json(silent=True)
        allowed = {'name', 'kind', 'instructions', 'enabled'}
        if not isinstance(payload, dict) or set(payload) - allowed or not {'name', 'kind', 'instructions'} <= set(payload):
            return jsonify(error='请求参数无效'), 400
        try:
            row = save_skill(get_db(), g.user['class_id'], payload['name'], payload['kind'],
                             payload['instructions'], payload.get('enabled', True))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except sqlite3.IntegrityError:
            return jsonify(error='技能名称已存在'), 409
        return jsonify(skill=dict(row)), 201

    @app.put('/api/tutoring/skills/<int:skill_id>')
    @tutoring_enabled
    @login_required(role='teacher')
    def update_tutoring_skill(skill_id):
        payload = request.get_json(silent=True)
        allowed = {'name', 'kind', 'instructions', 'enabled'}
        if not isinstance(payload, dict) or set(payload) - allowed or not {'name', 'kind', 'instructions', 'enabled'} <= set(payload):
            return jsonify(error='请求参数无效'), 400
        try:
            row = save_skill(get_db(), g.user['class_id'], payload['name'], payload['kind'],
                             payload['instructions'], payload['enabled'], skill_id)
        except LookupError:
            abort(404)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except sqlite3.IntegrityError:
            return jsonify(error='技能名称已存在'), 409
        return jsonify(skill=dict(row))

    @app.delete('/api/tutoring/skills/<int:skill_id>')
    @tutoring_enabled
    @login_required(role='teacher')
    def remove_tutoring_skill(skill_id):
        try:
            delete_skill(get_db(), g.user['class_id'], skill_id)
        except LookupError:
            abort(404)
        except ValueError as error:
            return jsonify(error=str(error)), 409
        return jsonify(status='deleted')

    @app.get('/api/tutoring/sessions')
    @tutoring_enabled
    @login_required(role='student')
    def tutoring_sessions():
        rows = get_db().execute(
            "SELECT id,title,created_at,updated_at FROM tutoring_sessions "
            "WHERE owner_id=? AND class_id=? ORDER BY updated_at DESC,id DESC",
            (g.user['id'], g.user['class_id']),
        ).fetchall()
        return jsonify(sessions=[dict(row) for row in rows])

    @app.post('/api/tutoring/sessions')
    @tutoring_enabled
    @login_required(role='student')
    def create_tutoring_session():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or set(payload) - {'title'}:
            return jsonify(error='请求参数无效'), 400
        title = payload.get('title', '新会话')
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 100:
            return jsonify(error='会话名称须为 1–100 个字符'), 400
        db = get_db()
        cursor = db.execute(
            "INSERT INTO tutoring_sessions(owner_id,class_id,title) VALUES (?,?,?)",
            (g.user['id'], g.user['class_id'], title.strip()),
        )
        db.commit()
        row = db.execute(
            "SELECT id,title,created_at,updated_at FROM tutoring_sessions WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return jsonify(session=dict(row)), 201

    @app.get('/api/tutoring/sessions/<int:session_id>')
    @tutoring_enabled
    @login_required(role='student')
    def get_tutoring_session(session_id):
        db = get_db()
        session = db.execute(
            "SELECT id,title,created_at,updated_at FROM tutoring_sessions "
            "WHERE id=? AND owner_id=? AND class_id=?",
            (session_id, g.user['id'], g.user['class_id']),
        ).fetchone()
        if session is None:
            abort(404)
        messages = db.execute(
            "SELECT id,role,content,status,citations_json,error_code,created_at "
            "FROM tutoring_messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        import json
        return jsonify(session=dict(session), messages=[{
            'id': row['id'], 'role': row['role'], 'content': row['content'], 'status': row['status'],
            'citations': json.loads(row['citations_json']) if row['role'] == 'assistant' else [],
            'error_code': row['error_code'], 'created_at': row['created_at'],
        } for row in messages])

    @app.delete('/api/tutoring/sessions/<int:session_id>')
    @tutoring_enabled
    @login_required(role='student')
    def delete_tutoring_session(session_id):
        db = get_db()
        db.execute('BEGIN IMMEDIATE')
        session = db.execute(
            "SELECT id FROM tutoring_sessions WHERE id=? AND owner_id=? AND class_id=?",
            (session_id, g.user['id'], g.user['class_id']),
        ).fetchone()
        if session is None:
            db.rollback()
            abort(404)
        db.execute("DELETE FROM tutoring_messages WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM tutoring_sessions WHERE id=?", (session_id,))
        db.commit()
        return jsonify(status='deleted')

    @app.post('/api/tutoring/sessions/<int:session_id>/messages')
    @tutoring_enabled
    @login_required(role='student')
    def create_tutoring_message(session_id):
        if not app.config['KNOWLEDGE_ENABLED']:
            abort(404)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) - {'question', 'mode'}:
            return jsonify(error='请求参数无效'), 400
        question, mode = payload.get('question'), payload.get('mode', 'hybrid')
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            return jsonify(error='请输入 1–2000 个字符的问题'), 400
        if not isinstance(mode, str) or mode not in ('keyword', 'vector', 'hybrid'):
            return jsonify(error='检索模式无效'), 400

        db = get_db()
        db.execute('BEGIN IMMEDIATE')
        session = db.execute(
            "SELECT id,title FROM tutoring_sessions WHERE id=? AND owner_id=? AND class_id=?",
            (session_id, g.user['id'], g.user['class_id']),
        ).fetchone()
        if session is None:
            db.rollback()
            abort(404)
        if db.execute(
                "SELECT 1 FROM tutoring_messages WHERE session_id=? AND status='pending' LIMIT 1",
                (session_id,),
        ).fetchone():
            db.rollback()
            return jsonify(error='会话问题正在处理中，请稍后重试'), 409
        cursor = db.execute(
            "INSERT INTO tutoring_messages(session_id,role,content,status) VALUES (?, 'user', ?, 'pending')",
            (session_id, question.strip()),
        )
        user_message_id = cursor.lastrowid
        if session['title'] == '新会话':
            db.execute("UPDATE tutoring_sessions SET title=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (question.strip()[:100], session_id))
        db.commit()

        try:
            guidance = active_guidance(db, g.user['class_id'])
            result = tutoring_answer(db, app.extensions['knowledge'], app.extensions['answerer'],
                g.user['class_id'], question.strip(), mode, guidance['teacher_prompt'], guidance['skill'],
                app.extensions['knowledge_fallback_answerer'])
            if result['status'] == 'insufficient_evidence':
                db.execute('BEGIN IMMEDIATE')
                auth_state = _tutoring_request_state(db, g.user['id'], g.user['class_id'],
                                                     g.access_token_id, session_id)
                if auth_state is not None:
                    db.rollback()
                    raise PermissionError(auth_state)
                db.execute("UPDATE tutoring_messages SET status='completed' WHERE id=?", (user_message_id,))
                db.execute("INSERT INTO tutoring_messages(session_id,role,content,status,citations_json) "
                           "VALUES (?, 'assistant', ?, 'completed', '[]')",
                           (session_id, result['answer']))
                db.execute("UPDATE tutoring_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,))
                db.commit()
                return jsonify(status='insufficient_evidence', answer=result['answer'], citations=[])

            db.execute('BEGIN IMMEDIATE')
            auth_state = _tutoring_request_state(db, g.user['id'], g.user['class_id'],
                                                 g.access_token_id, session_id)
            if auth_state is not None:
                db.rollback()
                raise PermissionError(auth_state)
            cursor = db.execute(
                "INSERT INTO tutoring_messages(session_id,role,content,status,citations_json) "
                "VALUES (?, 'assistant', ?, 'completed', ?)",
                (session_id, result['answer'], json.dumps(result['citations'], ensure_ascii=False)),
            )
            assistant_message_id = cursor.lastrowid
            db.execute("UPDATE tutoring_messages SET status='completed' WHERE id=?", (user_message_id,))
            db.execute("UPDATE tutoring_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,))
            db.commit()
        except PermissionError as error:
            _fail_tutoring_message(db, user_message_id, 'authorization_changed')
            if str(error) == 'unauthenticated':
                return jsonify(error='登录状态已过期'), 401
            return jsonify(error='班级或会话权限已变化，请重新进入会话'), 403
        except EvidenceChanged as error:
            _fail_tutoring_message(db, user_message_id, 'evidence_changed')
            return jsonify(error=str(error)), 409
        except InvalidAnswer as error:
            _fail_tutoring_message(db, user_message_id, 'answer_validation_failed')
            return jsonify(error=str(error)), 502
        except Unavailable as error:
            _fail_tutoring_message(db, user_message_id, 'model_unavailable')
            return jsonify(error=str(error)), 503
        except Exception:
            _fail_tutoring_message(db, user_message_id, 'processing_failed')
            return jsonify(error='辅导处理失败，请稍后重试'), 503

        def events():
            yield _sse('meta', {'message_id': assistant_message_id, 'status': 'completed'})
            text = result['answer']
            for start in range(0, len(text), 48):
                yield _sse('delta', {'text': text[start:start + 48]})
            yield _sse('citations', {'items': result['citations']})
            yield _sse('done', {'message_id': assistant_message_id})

        response = Response(events(), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache, no-transform'
        response.headers['X-Accel-Buffering'] = 'no'
        return response

    @app.post('/api/knowledge/ask')
    @login_required()
    def ask():
        question, mode, _ = inputs(question=True)
        guidance = active_guidance(get_db(), g.user['class_id'])
        result = tutoring_answer(
            get_db(), knowledge, app.extensions['answerer'], g.user['class_id'], question, mode,
            guidance['teacher_prompt'], guidance['skill'], app.extensions['knowledge_fallback_answerer'])
        # Long inference must not return evidence after a revocation or class move.
        token = get_db().execute('SELECT * FROM access_tokens WHERE id=?', (g.access_token_id,)).fetchone()
        if token is None or token['revoked_at'] is not None or token['expires_at'] <= time.time():
            return jsonify(error='登录状态已过期'), 401
        user = get_db().execute('SELECT class_id FROM users WHERE id=?', (g.user['id'],)).fetchone()
        if user is None or user['class_id'] != g.user['class_id']:
            abort(403)
        return jsonify(result)

    @app.cli.command('reindex-materials')
    @click.option('--material-id', type=int)
    @click.option('--all', 'all_materials', is_flag=True, help='Also rebuild published materials.')
    def reindex(material_id, all_materials):
        db = get_db()
        sql = "SELECT id FROM materials WHERE index_status='indexed'"
        args = []
        if material_id is not None:
            sql += ' AND id=?'
            args.append(material_id)
        elif not all_materials:
            sql += " AND search_index_status IN ('pending','failed')"
        failed = 0
        for row in db.execute(sql, args).fetchall():
            try:
                ok = knowledge.index(db, row['id'])
            except Busy:
                ok = False
            click.echo(f"{row['id']}: {'indexed' if ok else 'failed/busy'}")
            failed += not ok
        if failed:
            raise click.ClickException(f'{failed} material(s) failed or busy; retry after checking status')


def _sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _fail_tutoring_message(db, message_id, error_code):
    try:
        db.execute("UPDATE tutoring_messages SET status='failed',error_code=? WHERE id=? AND status='pending'",
                   (error_code, message_id))
        db.commit()
    except sqlite3.Error:
        db.rollback()


def _tutoring_request_state(db, user_id, class_id, token_id, session_id):
    now = int(time.time())
    token = db.execute(
        "SELECT revoked_at,expires_at,user_id FROM access_tokens WHERE id=?", (token_id,)
    ).fetchone()
    user = db.execute("SELECT class_id,role FROM users WHERE id=?", (user_id,)).fetchone()
    session = db.execute(
        "SELECT 1 FROM tutoring_sessions WHERE id=? AND owner_id=? AND class_id=?",
        (session_id, user_id, class_id),
    ).fetchone()
    if not token or token['user_id'] != user_id or token['revoked_at'] is not None or token['expires_at'] <= now:
        return 'unauthenticated'
    if not user or user['role'] != 'student' or user['class_id'] != class_id or not session:
        return 'forbidden'
    return None
