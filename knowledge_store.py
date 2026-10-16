"""
飼養知識 chunk 的向量資料庫包裝 —— 跟 vector_store.py（商品照片用）是同一個
設計、同一個 Chroma 實例，但開一個獨立的 collection，不會跟商品圖片向量混在一起。
"""

import os

import chromadb
from chromadb.config import Settings

CHROMA_PATH = os.environ.get("GECKO_SHOP_CHROMA_PATH", "chroma_store")

_client = chromadb.PersistentClient(
    path=CHROMA_PATH,
    settings=Settings(anonymized_telemetry=False),
)

_collection = _client.get_or_create_collection(
    name="knowledge_chunks",
    metadata={"hnsw:space": "cosine"},
)


def upsert_chunk(chunk_id: str, vector, text: str, metadata: dict) -> None:
    _collection.upsert(
        ids=[chunk_id],
        embeddings=[vector.tolist() if hasattr(vector, "tolist") else list(vector)],
        documents=[text],
        metadatas=[metadata],
    )


def query_similar(vector, top_k: int = 3) -> list[dict]:
    """回傳最相關的前 k 個知識片段：[{chunk_id, text, species, topic, source, distance}, ...]，
    distance 越小越相關。"""
    result = _collection.query(
        query_embeddings=[vector.tolist() if hasattr(vector, "tolist") else list(vector)],
        n_results=top_k,
    )
    matches = []
    ids = result.get("ids", [[]])[0]
    distances = result.get("distances", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    for chunk_id, distance, text, meta in zip(ids, distances, documents, metadatas):
        matches.append(
            {
                "chunk_id": chunk_id,
                "text": text,
                "species": meta.get("species", ""),
                "topic": meta.get("topic", ""),
                "source": meta.get("source", ""),
                "distance": distance,
            }
        )
    return matches


def delete_by_source(source: str) -> None:
    """重新 ingest 同一個檔案前先清掉舊的 chunk，避免刪掉/改名標題後舊片段變成殭屍資料留在庫裡。"""
    _collection.delete(where={"source": source})


def count() -> int:
    return _collection.count()
