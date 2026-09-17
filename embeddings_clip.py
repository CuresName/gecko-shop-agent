"""
真正的 CLIP 版本 —— 等你把這個專案搬到有網路的地方(自己電腦、Railway...)時，
把這個檔案的內容整個蓋掉 embeddings.py 就好，其他檔案完全不用動。

用法：
    1. pip install sentence-transformers
    2. 把這份內容存成 embeddings.py（蓋掉原本的顏色直方圖版本）
    3. rm -rf chroma_store（舊的向量是用舊算法算的，格式不相容，要重算）
    4. 把 models.py 裡所有 Photo.is_embedded 改回 False（或直接刪掉 gecko_shop.db 重新跑 seed）
    5. python backfill_embeddings.py 重新算一次特徵
    6. python validate_phase2.py 應該就會看到橘化排橘化前面、雪花排雪花前面了
"""

import numpy as np
from PIL import Image
from sentence_transformers import SentenceTransformer

# 第一次執行會自動從 Hugging Face 下載模型權重（約 600MB），之後會快取在本機，
# 不用每次都重新下載。這就是為什麼這個沙盒環境跑不動的原因——網路連不到 huggingface.co。
_model = SentenceTransformer("clip-ViT-B-32")

VECTOR_SIZE = 512  # clip-ViT-B-32 輸出的維度


def encode_image(path: str) -> np.ndarray:
    """用真正的 CLIP 模型把圖片轉成語意層級的特徵向量。"""
    img = Image.open(path).convert("RGB")
    vector = _model.encode(img, convert_to_numpy=True)
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


def encode_text(text: str) -> np.ndarray:
    """加碼：CLIP 同時懂文字，之後想做「純文字搜尋商品」(不用照片)也可以用這個，
    例如客人打「有沒有橘色的」，直接把這句話編碼去跟商品照片的向量比對。
    """
    vector = _model.encode(text, convert_to_numpy=True)
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector
