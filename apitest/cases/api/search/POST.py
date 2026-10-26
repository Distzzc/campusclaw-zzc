"""POST /api/search: verify class-scoped hits against current SQLite source rows."""
import json


def run(ctx):
    token = ctx.require_student()
    # Try stable subject terms instead of assuming one exact material exists.
    for query in ("物理", "光合作用", "能量", "报到", "运动"):
        status, payload, _ = ctx.request("POST", "/api/search", token=token,
                                         body={"query": query, "mode": "keyword", "limit": 5})
        ctx.require(status == 200, f"检索“{query}”应返回 200，实际为 {status}")
        ctx.require(isinstance(payload, dict) and payload.get("mode") == "keyword",
                    "检索响应缺少正确 mode")
        results = payload.get("results")
        ctx.require(isinstance(results, list), "检索响应 results 应为数组")
        if results:
            ctx.search_query, ctx.search_result = query, results[0]
            break
    ctx.require(ctx.search_result is not None, "候选词均未命中本班已索引材料")

    hit = ctx.search_result
    ctx.require(hit.get("material_id") and hit.get("chunk_id") and hit.get("content_version"),
                "检索命中缺少材料/片段/版本标识")
    rows = ctx.query_db(
        "SELECT c.id,c.class_id,c.content_version,c.text,c.location,m.title,m.filename,"
        "m.class_id AS material_class,m.search_index_status,m.index_status "
        "FROM knowledge_chunks c JOIN materials m ON m.id=c.material_id "
        "WHERE c.id=? AND c.material_id=? AND c.content_version=m.content_version "
        "AND c.generation=m.index_generation AND m.search_index_status='indexed' AND m.index_status='indexed'",
        (hit["chunk_id"], hit["material_id"]),
    )
    ctx.require(len(rows) == 1, "返回片段在数据库中不存在或不唯一")
    row = rows[0]
    ctx.require(row["class_id"] == ctx.user["class_id"] == row["material_class"],
                "检索结果班级与认证用户/材料数据库记录不一致")
    ctx.require(row["search_index_status"] == "indexed" and row["index_status"] == "indexed",
                "检索结果对应材料在数据库中并非可检索状态")
    ctx.require(row["content_version"] == hit["content_version"], "检索返回的内容版本与数据库不一致")
    ctx.require(row["text"] == hit["excerpt"], "检索返回片段与数据库原文不一致")
    ctx.require(row["title"] == hit["title"] and row["filename"] == hit["filename"],
                "检索返回来源标题/文件名与数据库不一致")
    ctx.require(json.loads(row["location"]) == hit["location"],
                "检索来源定位与数据库不一致")

    source_status, source, _ = ctx.request("GET", hit["source_url"], token=token)
    ctx.require(source_status == 200 and source.get("excerpt") == row["text"],
                "受保护来源 API 与搜索结果/数据库原文不一致")
