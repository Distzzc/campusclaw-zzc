import sqlite3

from knowledge.schema import migrate, ensure_fts, fts_table


def test_migration_preserves_old_materials_and_is_repeatable():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE classes(id INTEGER PRIMARY KEY);
        INSERT INTO classes VALUES (1);
        CREATE TABLE materials(id INTEGER PRIMARY KEY, index_status TEXT, storage_path TEXT);
        INSERT INTO materials VALUES (1, 'indexed', '/old/file');
    """)
    migrate(db)
    migrate(db)
    assert db.execute("SELECT index_status, storage_path, search_index_status, index_generation FROM materials").fetchone() == (
        'indexed', '/old/file', 'pending', 0)
    table = ensure_fts(db, 1)
    db.execute(f"INSERT INTO {table}(chunk_id, terms) VALUES ('one','hello')")
    assert db.execute(f"SELECT chunk_id FROM {table} WHERE {table} MATCH ?", ('hello',)).fetchone() == ('one',)


def test_table_names_require_server_integer():
    import pytest
    for invalid in ('1; DROP TABLE materials', True, -1):
        with pytest.raises(ValueError):
            fts_table(invalid)
