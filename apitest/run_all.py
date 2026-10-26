#!/usr/bin/env python3
"""Run all endpoint cases and write a local-only HTML report."""
from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
APITEST = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from apitest.common import ApiTestContext, CheckFailure, CheckSkipped  # noqa: E402


ORDER = {
    "api/auth/login/POST.py": 0,
    "api/search/POST.py": 10,
    "api/tutoring/config/GET.py": 20,
    "api/tutoring/config/PUT.py": 21,
    "api/tutoring/skills/GET.py": 30,
    "api/tutoring/skills/POST.py": 31,
    "api/tutoring/sessions/POST.py": 40,
    "api/tutoring/sessions/<session_id>/messages/POST.py": 41,
}


def discover_cases() -> list[Path]:
    cases_root = APITEST / "cases"
    found = sorted(cases_root.rglob("*.py"))
    if not found:
        raise SystemExit(f"未找到接口用例：{cases_root}")
    return sorted(found, key=lambda path: (
        ORDER.get(path.relative_to(cases_root).as_posix(), 100),
        path.relative_to(cases_root).as_posix(),
    ))


def endpoint_for(case: Path) -> tuple[str, str]:
    relative = case.relative_to(APITEST / "cases")
    method = case.stem.upper()
    path = "/" + "/".join(relative.parts[:-1])
    return method, path


def run_case(case: Path, context: ApiTestContext) -> dict:
    relative = case.relative_to(APITEST / "cases").as_posix()
    method, endpoint = endpoint_for(case)
    started = time.perf_counter()
    try:
        module_name = "apitest_case_" + str(abs(hash(relative)))
        spec = importlib.util.spec_from_file_location(module_name, case)
        if spec is None or spec.loader is None:
            raise CheckFailure("无法加载接口用例脚本")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "run", None)):
            raise CheckFailure("接口脚本必须提供 run(ctx) 函数")
        module.run(context)
        status, message = "passed", "响应字段与数据库记录核对通过"
    except CheckSkipped as error:
        status, message = "skipped", str(error)
    except Exception as error:  # Keep reports useful without logging request credentials or bodies.
        status, message = "failed", str(error) or type(error).__name__
    return {
        "case": relative,
        "method": method,
        "endpoint": endpoint,
        "status": status,
        "message": message,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def write_html_report(results: list[dict], report_path: Path, base_url: str) -> None:
    passed = sum(row["status"] == "passed" for row in results)
    failed = sum(row["status"] == "failed" for row in results)
    skipped = sum(row["status"] == "skipped" for row in results)
    rows = []
    for result in results:
        rows.append(
            "<tr class='{status}'><td>{method}</td><td>{endpoint}</td><td>{case}</td>"
            "<td>{status}</td><td>{duration} ms</td><td>{message}</td></tr>".format(
                status=html.escape(result["status"]),
                method=html.escape(result["method"]),
                endpoint=html.escape(result["endpoint"]),
                case=html.escape(result["case"]),
                duration=result["duration_ms"],
                message=html.escape(result["message"]),
            )
        )
    generated = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>API 测试报告</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:32px;color:#242424;background:#f6f7f8}}
main{{max-width:1200px;margin:auto}}h1{{margin-bottom:4px}}.meta{{color:#666}}.summary{{display:flex;gap:12px;margin:22px 0}}
.badge{{padding:10px 16px;background:white;border-radius:9px;box-shadow:0 1px 4px #0001}}table{{width:100%;border-collapse:collapse;background:white}}
th,td{{padding:11px;text-align:left;border-bottom:1px solid #e6e8eb;vertical-align:top}}th{{background:#edf0f2}}
.passed td:nth-child(4){{color:#187943;font-weight:700}}.failed td:nth-child(4){{color:#b42318;font-weight:700}}
.skipped td:nth-child(4){{color:#9a6700;font-weight:700}}td:last-child{{max-width:420px;overflow-wrap:anywhere}}
</style></head><body><main><h1>CampusClaw API 测试报告</h1>
<p class="meta">目标：{html.escape(base_url)}　生成时间：{html.escape(generated)}</p>
<div class="summary"><span class="badge">通过：{passed}</span><span class="badge">失败：{failed}</span><span class="badge">跳过：{skipped}</span><span class="badge">总数：{len(results)}</span></div>
<table><thead><tr><th>方法</th><th>接口</th><th>脚本</th><th>结果</th><th>耗时</th><th>核验</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table><p class="meta">报告不包含访问令牌、请求密码或完整响应正文。</p></main></body></html>"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Docker 中 CampusClaw API 的端到端核验")
    parser.add_argument("--base-url", help="API 根地址，默认读取 APITEST_BASE_URL 或 http://localhost:5001")
    parser.add_argument("--container", help="应用容器名，默认读取 APITEST_CONTAINER 或 campusclaw-zzc-app-1")
    args = parser.parse_args()
    if args.base_url:
        import os
        os.environ["APITEST_BASE_URL"] = args.base_url
    if args.container:
        import os
        os.environ["APITEST_CONTAINER"] = args.container

    context = ApiTestContext()
    cases = discover_cases()
    results = []
    print(f"API target: {context.base_url}")
    print(f"Database container: {context.container}")
    for case in cases:
        result = run_case(case, context)
        results.append(result)
        symbol = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[result["status"]]
        print(f"[{symbol}] {result['method']} {result['endpoint']} ({result['duration_ms']} ms): {result['message']}")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = APITEST / "reports" / f"report_{stamp}.html"
    write_html_report(results, report_path, context.base_url)
    print(f"HTML report: {report_path.relative_to(ROOT)}")
    return 1 if any(row["status"] == "failed" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
