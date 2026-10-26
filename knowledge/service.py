"""The relational store is authoritative for scope, versions and publication."""
import json
import time
from pathlib import Path

from .adapters import Unavailable
from .schema import ensure_fts, fts_table
from .text import extract, split_units, terms, ExtractionError


class Busy(Unavailable):
    pass


class Knowledge:
    def __init__(self, config, embedding, vectors):
        self.config, self.embedding, self.vectors = config, embedding, vectors

    def index(self, db, material_id):
        now = time.time()
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM materials WHERE id=?', (material_id,)).fetchone()
        if not row or row['index_status'] != 'indexed':
            db.rollback()
            return False
        if row['index_lease_until'] > now:
            db.rollback()
            raise Busy('材料正在建立索引，请稍后重试')
        generation = row['index_generation'] + 1
        db.execute("UPDATE materials SET search_index_status='pending', search_error=NULL, "
                   'index_generation=?, index_lease_until=? WHERE id=?',
                   (generation, now + self.config['INDEX_TIMEOUT'], material_id))
        db.commit()
        key = self.embedding.key
        try:
            path = Path(row['storage_path']).resolve()
            path.relative_to(Path(self.config['STORAGE_PATH']).resolve())
            if path.stat().st_size > self.config['MAX_CONTENT_LENGTH']:
                raise ExtractionError('材料过大，无法建立索引')
            units = extract(path, row['filename'])
            if sum(len(text) for text, _ in units) > self.config['INDEX_MAX_CHARACTERS']:
                raise ExtractionError('材料正文超过处理上限')
            version, chunks = split_units(units, self.embedding.tokenizer,
                self.embedding.max_tokens, material_id, key)
            embeddings = self.embedding.encode([c['text'] for c in chunks])
            if len(embeddings) != len(chunks) or not embeddings or not embeddings[0]:
                raise ValueError('invalid embeddings')
            dimensions = len(embeddings[0])
            if any(len(v) != dimensions for v in embeddings):
                raise ValueError('mixed dimensions')
            if time.time() - now >= self.config['INDEX_TIMEOUT']:
                raise Unavailable('索引处理超时，请稍后重试')
            self.vectors.write(key, chunks, embeddings, material_id, row['class_id'], generation)
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('SELECT index_generation, index_lease_until FROM materials WHERE id=?',
                                (material_id,)).fetchone()
            if active['index_generation'] != generation or active['index_lease_until'] < time.time():
                raise Busy('索引作业已过期，请重新执行')
            table = ensure_fts(db, row['class_id'])
            db.execute(f'DELETE FROM {table} WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE material_id=?)', (material_id,))
            db.execute('DELETE FROM knowledge_chunks WHERE material_id=?', (material_id,))
            for chunk in chunks:
                db.execute('INSERT INTO knowledge_chunks VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (chunk['id'], material_id, row['class_id'], version, generation,
                     chunk['ordinal'], chunk['text'], json.dumps(chunk['location'], ensure_ascii=False), key, dimensions))
                db.execute(f'INSERT INTO {table}(chunk_id, terms) VALUES (?,?)',
                           (chunk['id'], ' '.join(terms(chunk['text']))))
            db.execute("UPDATE materials SET search_index_status='indexed', content_version=?, "
                       'index_lease_until=0 WHERE id=? AND index_generation=?', (version, material_id, generation))
            db.commit()
        except Exception as error:
            db.rollback()
            try:
                self.vectors.remove_generation(key, material_id, generation)
            except Exception:
                pass  # SQL publication gate excludes these orphans; CLI retry prunes them.
            message = str(error) if isinstance(error, (ExtractionError, Unavailable)) else '正文索引失败，请检查文件或本地模型后重试'
            db.execute("UPDATE materials SET search_index_status='failed', search_error=?, index_lease_until=0 "
                       'WHERE id=? AND index_generation=?', (message, material_id, generation))
            db.commit()
            return False
        try:
            self.vectors.prune(material_id, generation)
        except Exception:
            pass  # Current generation check makes old vectors invisible.
        return True

    def passage(self, db, class_id, chunk_id):
        return db.execute('''SELECT c.*, m.title, m.filename FROM knowledge_chunks c
            JOIN materials m ON m.id=c.material_id
            WHERE c.id=? AND c.class_id=? AND m.class_id=?
            AND m.index_status='indexed' AND m.search_index_status='indexed'
            AND m.content_version=c.content_version AND m.index_generation=c.generation''',
            (chunk_id, class_id, class_id)).fetchone()

    def search(self, db, class_id, query, mode, limit=5):
        try:
            routes = []
            if mode in ('keyword', 'hybrid'):
                words = terms(query)
                table = fts_table(class_id)
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
                ids = []
                if words and exists:
                    expression = ' OR '.join('"' + word.replace('"', '""') + '"' for word in words)
                    ids = [r[0] for r in db.execute(f'''SELECT f.chunk_id FROM {table} f
                        JOIN knowledge_chunks c ON c.id=f.chunk_id JOIN materials m ON m.id=c.material_id
                        WHERE {table} MATCH ? AND c.class_id=? AND m.class_id=?
                        AND m.search_index_status='indexed' AND m.index_status='indexed'
                        AND m.content_version=c.content_version AND m.index_generation=c.generation
                        ORDER BY bm25({table}), f.chunk_id LIMIT 50''', (expression, class_id, class_id))]
                routes.append(ids)
            if mode in ('vector', 'hybrid'):
                # Do not silently truncate a long query at the model boundary.
                token_count = len(self.embedding.tokenizer.encode(query, add_special_tokens=False))
                budget = self.embedding.max_tokens - self.embedding.tokenizer.num_special_tokens_to_add(pair=False)
                if token_count > budget:
                    _, pieces = split_units([(query, {})], self.embedding.tokenizer,
                                             self.embedding.max_tokens, 0, self.embedding.key)
                    vectors = self.embedding.encode([c['text'] for c in pieces])
                    import math
                    vector = [sum(v[i] for v in vectors) / len(vectors) for i in range(len(vectors[0]))]
                    norm = math.sqrt(sum(x*x for x in vector)) or 1
                    vector = [x/norm for x in vector]
                else:
                    vector = self.embedding.encode([query])[0]
                candidates = self.vectors.query(self.embedding.key, vector, class_id, 50)
                ids = []
                for chunk_id, generation, score in candidates:
                    row = self.passage(db, class_id, chunk_id)
                    if (row is not None and row['generation'] == generation
                            and row['model_key'] == self.embedding.key
                            and score >= self.config['VECTOR_MIN_SIMILARITY']):
                        ids.append(chunk_id)
                routes.append(ids)
            rows = {}
            valid_routes = []
            for route in routes:
                valid = []
                for chunk_id in dict.fromkeys(route):
                    row = self.passage(db, class_id, chunk_id)
                    if row is not None:
                        rows[chunk_id] = row
                        valid.append(chunk_id)
                valid_routes.append(valid)
            ids = fuse(valid_routes) if mode == 'hybrid' else valid_routes[0]
            return [source(rows[key], rank=i+1) for i, key in enumerate(ids[:limit])]
        except Unavailable:
            raise
        except Exception as error:
            raise Unavailable('检索服务暂不可用，请稍后重试') from error


def fuse(routes):
    scores = {}
    for route in routes:
        for rank, key in enumerate(dict.fromkeys(route), 1):
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))


def source(row, rank=None):
    result = dict(chunk_id=row['id'], content_version=row['content_version'],
                  material_id=row['material_id'], title=row['title'], filename=row['filename'],
                  excerpt=row['text'], location=json.loads(row['location']),
                  source_url=f"/api/knowledge/chunks/{row['id']}",
                  download_url=f"/api/materials/{row['material_id']}/download")
    if rank is not None:
        result['rank'] = rank
    return result


def parameters(payload, question=False):
    field = 'question' if question else 'query'
    allowed = {field, 'mode', 'class_id'} | (set() if question else {'limit'})
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('请求参数无效')
    query = payload.get(field)
    mode, limit = payload.get('mode', 'hybrid'), payload.get('limit', 5)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
        raise ValueError('请输入 1–2000 字符的问题或检索词')
    if not isinstance(mode, str) or mode not in ('keyword', 'vector', 'hybrid'):
        raise ValueError('检索模式无效')
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('结果数量必须是 1–20 的整数')
    return query.strip(), mode, limit
