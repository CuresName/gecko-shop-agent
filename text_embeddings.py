"""
飼養知識的文字 embedding —— 跟 embeddings.py（圖片用 CLIP）是同一個設計理由，
獨立成一個檔案：以後想換模型，只要改這裡，knowledge_store.py/ingest_knowledge.py
完全不用動。

故意不跟 embeddings.py 共用 CLIP 的 encode_text——CLIP 的文字編碼器是為了
「短短一句圖片說明」訓練的，不是為了「一整段中文飼養知識」這種長文本語意比對
設計的，拿來做知識庫檢索效果會比專門的文字 embedding 模型差很多。

選輕量多語言模型（而不是計畫裡原本提的 bge-m3）：bge-m3 體積大很多，Railway
目前的記憶體已經常常被 CLIP 吃到快滿（見專案筆記的 OOM 事故），這個模型小很多
（~470MB），中文語意比對的表現也夠用。
"""

import os

from sentence_transformers import SentenceTransformer

_MODEL_NAME = os.environ.get("GECKO_SHOP_TEXT_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
_model = SentenceTransformer(_MODEL_NAME)


def encode_text(text: str) -> list[float]:
    """回傳正規化過的向量（已經除以長度），knowledge_store.py 用 cosine 距離比對。"""
    vector = _model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
    return vector.tolist()
