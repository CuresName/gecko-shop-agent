"""
向量資料庫（Chroma）包裝
========================
只負責「存向量、找最像的前 k 個」，不管向量是怎麼算出來的——
這樣以後把 embeddings.py 換成真的 CLIP，這個檔案完全不用改。
"""

import os

import chromadb
from chromadb.config import Settings

# 可以用環境變數覆寫（驗證腳本用這個切到獨立的測試向量庫，不會動到正式資料）
CHROMA_PATH = os.environ.get("GECKO_SHOP_CHROMA_PATH", "chroma_store")

_client = chromadb.PersistentClient(
    path=CHROMA_PATH,
    settings=Settings(anonymized_telemetry=False),  # 不需要，也避免對外連線
)

_collection = _client.get_or_create_collection(
    name="gecko_photos",
    metadata={"hnsw:space": "cosine"},
)


def add_photo(photo_id: int, product_id: int, vector, extra_metadata: dict | None = None) -> None:
    _collection.upsert(
        ids=[str(photo_id)],
        embeddings=[vector.tolist() if hasattr(vector, "tolist") else list(vector)],
        metadatas=[{"product_id": product_id, **(extra_metadata or {})}],
    )


def query_similar(vector, top_k: int = 5) -> list[dict]:
    """回傳最像的前 k 筆：[{photo_id, product_id, distance}, ...]，distance 越小越像。"""
    result = _collection.query(
        query_embeddings=[vector.tolist() if hasattr(vector, "tolist") else list(vector)],
        n_results=top_k,
    )
    matches = []
    ids = result.get("ids", [[]])[0]
    distances = result.get("distances", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    for photo_id, distance, meta in zip(ids, distances, metadatas):
        matches.append(
            {
                "photo_id": int(photo_id),
                "product_id": meta["product_id"],
                "distance": distance,
            }
        )
    return matches


def count() -> int:
    return _collection.count()
