"""Lazy local model and persistent vector adapters; no runtime downloads."""
import hashlib
import threading
from pathlib import Path


class Unavailable(RuntimeError):
    pass


class LocalEmbedding:
    def __init__(self, path, revision):
        self.path = path
        self.key = hashlib.sha256(f'{path}:{revision}'.encode()).hexdigest()
        self._model = None
        self._lock = threading.Lock()

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                if not self.path or not Path(self.path).is_dir():
                    raise Unavailable('本地嵌入模型未准备好')
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.path, local_files_only=True, trust_remote_code=False)
        return self._model

    @property
    def tokenizer(self):
        return self.model.tokenizer

    @property
    def max_tokens(self):
        return self.model.max_seq_length

    def encode(self, texts):
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


class ChromaVectors:
    def __init__(self, path):
        self.path = path
        self._client = None
        self._lock = threading.Lock()

    def collection(self, key):
        with self._lock:
            if self._client is None:
                import chromadb
                from chromadb.config import Settings
                self._client = chromadb.PersistentClient(path=self.path, settings=Settings(anonymized_telemetry=False))
            return self._client.get_or_create_collection(
                name='knowledge-' + hashlib.sha256(key.encode()).hexdigest()[:32],
                metadata={'hnsw:space': 'cosine'}, embedding_function=None)

    def write(self, key, chunks, embeddings, material_id, class_id, generation):
        col = self.collection(key)
        for start in range(0, len(chunks), 128):
            group = chunks[start:start + 128]
            col.upsert(ids=[f"{c['id']}:{generation}" for c in group],
                       embeddings=embeddings[start:start + 128],
                       metadatas=[{'chunk_id': c['id'], 'material_id': material_id,
                                   'class_id': class_id, 'generation': generation} for c in group])

    def remove_generation(self, key, material_id, generation):
        self.collection(key).delete(where={'$and': [{'material_id': material_id}, {'generation': generation}]})

    def prune(self, material_id, generation):
        # Include old model collections; use exact old generation ids, never
        # delete newer work from a worker whose lease has expired.
        if self._client is None:
            return
        for info in self._client.list_collections():
            if info.name.startswith('knowledge-'):
                self._client.get_collection(info.name, embedding_function=None).delete(
                    where={'$and': [{'material_id': material_id}, {'generation': {'$lt': generation}}]})

    def query(self, key, embedding, class_id, limit):
        result = self.collection(key).query(query_embeddings=[embedding],
            where={'class_id': class_id}, n_results=limit, include=['metadatas', 'distances'])
        return [(meta['chunk_id'], meta['generation'], 1 - distance)
                for meta, distance in zip(result['metadatas'][0], result['distances'][0])]
