"""Additive migrations; file availability remains independent of search."""


def fts_table(class_id):
    if type(class_id) is not int or class_id <= 0:
        raise ValueError("invalid class identity")
    return f"knowledge_fts_{class_id}"


def ensure_fts(db, class_id):
    table = fts_table(class_id)
    db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5(chunk_id UNINDEXED, terms)")
    return table


def migrate(db):
    # Fail at startup if the runtime does not provide FTS5.
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_check USING fts5(text)")
    db.execute("DROP TABLE temp.fts_check")
    columns = {row[1] for row in db.execute("PRAGMA table_info(materials)")}
    additions = {
        "search_index_status": "TEXT NOT NULL DEFAULT 'pending'",
        "search_error": "TEXT",
        "content_version": "TEXT",
        "index_generation": "INTEGER NOT NULL DEFAULT 0",
        "index_lease_until": "REAL NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE materials ADD COLUMN {name} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_chunks (
        id TEXT PRIMARY KEY, material_id INTEGER NOT NULL REFERENCES materials(id),
        class_id INTEGER NOT NULL REFERENCES classes(id), content_version TEXT NOT NULL,
        generation INTEGER NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL,
        location TEXT NOT NULL, model_key TEXT NOT NULL, dimensions INTEGER NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS chunks_scope ON knowledge_chunks(class_id, material_id)")
    for row in db.execute("SELECT id FROM classes").fetchall():
        ensure_fts(db, row[0])
    db.commit()
