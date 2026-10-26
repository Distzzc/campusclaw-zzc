"""Shared helpers for black-box API checks against the running Docker app."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class CheckFailure(AssertionError):
    pass


class CheckSkipped(RuntimeError):
    pass


class ApiTestContext:
    def __init__(self):
        self.base_url = os.environ.get("APITEST_BASE_URL", "http://localhost:5001").rstrip("/")
        self.container = os.environ.get("APITEST_CONTAINER", "campusclaw-zzc-app-1")
        self.database = os.environ.get("APITEST_DATABASE", "/app/data/app.db")
        self.username = os.environ.get("APITEST_USERNAME", "a_student")
        self.password = os.environ.get("APITEST_PASSWORD", "a-student-password")
        self.teacher_username = os.environ.get("APITEST_TEACHER_USERNAME", "a_teacher")
        self.teacher_password = os.environ.get("APITEST_TEACHER_PASSWORD", "a-teacher-password")
        self.token: str | None = None
        self.teacher_token: str | None = None
        self.user: dict | None = None
        self.teacher: dict | None = None
        self.config_before: dict | None = None
        self.search_result: dict | None = None
        self.search_query: str | None = None
        self.session: dict | None = None
        self.created_skill_id: int | None = None

    def request(self, method: str, path: str, *, body: dict | None = None,
                token: str | None = None, timeout: int = 180) -> tuple[int, object, dict[str, str]]:
        headers = {"Accept": "application/json, text/event-stream"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        hostname = (urlsplit(self.base_url).hostname or "").lower()
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
                  if hostname in {"localhost", "127.0.0.1", "::1"}
                  else urllib.request.build_opener())
        try:
            with opener.open(req, timeout=timeout) as response:
                raw = response.read()
                status = response.status
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
            response_headers = dict(error.headers.items())
        content_type = response_headers.get("Content-Type", "")
        text = raw.decode("utf-8", errors="replace")
        if "application/json" in content_type:
            try:
                payload: object = json.loads(text)
            except json.JSONDecodeError as error:
                raise CheckFailure(f"{method} {path} 返回了无效 JSON") from error
        else:
            payload = text
        return status, payload, response_headers

    def query_db(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a read-only SELECT against the app's SQLite DB inside its container."""
        if not sql.lstrip().upper().startswith("SELECT"):
            raise ValueError("API tests permit read-only SELECT database checks only")
        code = (
            "import json,sqlite3,sys; "
            "db=sqlite3.connect(sys.argv[1]); db.row_factory=sqlite3.Row; "
            "rows=db.execute(sys.argv[2],json.loads(sys.argv[3])).fetchall(); "
            "print(json.dumps([dict(r) for r in rows],ensure_ascii=False))"
        )
        completed = subprocess.run(
            ["docker", "exec", self.container, "python", "-c", code,
             self.database, sql, json.dumps(params, ensure_ascii=False)],
            capture_output=True, text=True, check=False,
        )
        if completed.returncode:
            detail = completed.stderr.strip().splitlines()[-1:] or ["docker exec failed"]
            raise CheckFailure(f"无法读取容器数据库（{detail[0]}）")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CheckFailure("容器数据库检查未返回有效 JSON") from error

    @staticmethod
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise CheckFailure(message)

    def require_student(self) -> str:
        if not self.token:
            raise CheckSkipped("学生登录未成功，依赖该令牌的用例跳过")
        return self.token

    def require_teacher(self) -> str:
        if not self.teacher_token:
            raise CheckSkipped("教师登录未成功，依赖该令牌的用例跳过")
        return self.teacher_token

    def require_search(self) -> dict:
        if not self.search_result:
            raise CheckSkipped("检索未找到可核验片段，依赖检索结果的用例跳过")
        return self.search_result

    def require_session(self) -> dict:
        if not self.session:
            raise CheckSkipped("会话创建未成功，依赖会话的用例跳过")
        return self.session
