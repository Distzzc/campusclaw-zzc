"""Teacher-managed, class-scoped tool permissions on the existing bearer auth."""
import time

from flask import abort, g, jsonify, request, has_request_context

from .registry import ToolRegistry, ToolError, BoundTools, enabled_ids


def configure(app, get_db, login_required):
    registry = ToolRegistry(app.config if app.config['TOOLS_ENABLED'] else {})
    app.extensions['tools'] = registry

    def active_permission(tool_id, user_id, class_id, token_id):
        row = get_db().execute(
            'SELECT 1 FROM users u JOIN access_tokens t ON t.user_id=u.id '
            'JOIN class_external_tools e ON e.class_id=u.class_id '
            'WHERE u.id=? AND u.class_id=? AND t.id=? AND t.revoked_at IS NULL '
            'AND t.expires_at>? AND e.tool_id=?',
            (user_id, class_id, token_id, int(time.time()), tool_id),
        ).fetchone()
        return row is not None

    def bound_tools():
        if not has_request_context() or not app.config['TOOLS_ENABLED'] or not g.get('user'):
            return None
        if 'campusclaw.bound_tools' not in request.environ:
            user_id, class_id, token_id = g.user['id'], g.user['class_id'], g.access_token_id
            request.environ['campusclaw.bound_tools'] = BoundTools(registry, enabled_ids(get_db(), class_id),
                lambda tool_id: active_permission(tool_id, user_id, class_id, token_id))
        return request.environ['campusclaw.bound_tools']

    for key in ('answerer', 'knowledge_fallback_answerer'):
        answerer = app.extensions.get(key)
        if answerer is not None:
            answerer.tool_provider = bound_tools

    def require_enabled():
        if not app.config['TOOLS_ENABLED']:
            abort(404)

    @app.get('/api/integrations')
    @login_required(role='teacher')
    def integration_catalog():
        require_enabled()
        if request.args.get('refresh') == '1':
            registry.catalog(refresh=True)
        payload = registry.public_catalog(enabled_ids(get_db(), g.user['class_id']))
        payload['mcp_endpoint'] = '/mcp' if app.config['MCP_SERVER_ENABLED'] else None
        payload['model_support'] = bool(app.config['QA_API_BASE_URL'] and app.config['QA_API_KEY']
                                        and app.config['QA_API_MODEL'])
        return jsonify(payload)

    @app.put('/api/integrations')
    @login_required(role='teacher')
    def save_integrations():
        require_enabled()
        payload = request.get_json(silent=True)
        if (not isinstance(payload, dict) or set(payload) != {'enabled_tools'}
                or not isinstance(payload['enabled_tools'], list) or len(payload['enabled_tools']) > 32
                or any(not isinstance(item, str) for item in payload['enabled_tools'])):
            return jsonify(error='请提交最多 32 项 enabled_tools'), 400
        tools, _ = registry.catalog()
        if set(payload['enabled_tools']) - ({tool['id'] for tool in tools} | enabled_ids(get_db(), g.user['class_id'])):
            return jsonify(error='包含不可用或未获准的工具，请刷新列表'), 400
        db = get_db()
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM class_external_tools WHERE class_id=?', (g.user['class_id'],))
        db.executemany('INSERT INTO class_external_tools(class_id,tool_id) VALUES (?,?)',
                       [(g.user['class_id'], item) for item in set(payload['enabled_tools'])])
        db.commit()
        return jsonify(status='saved', enabled_tools=sorted(set(payload['enabled_tools'])))

    @app.post('/api/integrations/tools/<path:tool_id>/call')
    @login_required(role='teacher')
    def test_integration_tool(tool_id):
        require_enabled()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'arguments'}:
            return jsonify(error='请提交 arguments 对象'), 400
        bound = bound_tools()
        model_name = next((name for name, tool in bound.tools.items() if tool['id'] == tool_id), None)
        if model_name is None:
            return jsonify(error='请先为本班启用该工具'), 403
        try:
            return jsonify(result=bound.call(model_name, payload['arguments']))
        except ToolError as error:
            return jsonify(error=str(error)), 400
