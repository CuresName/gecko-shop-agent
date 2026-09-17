"""
客人的問題 -> 候選商品清單
==========================
Phase 3 的第一步：不管客人是傳照片還是純打字問問題，都要先從資料庫「查出真的
存在的商品」，再把這份清單交給 LLM 去組成自然的回覆——LLM 只負責講話的方式，
「有什麼、多少錢、還有沒有貨」這些事實一律由這裡的查詢結果決定。

兩種情況：
1. 客人有附照片：用 Phase 2 的向量搜尋 (vector_store.query_similar) 找最像的商品，
   而且只留下相似度夠高的——信心不夠的直接濾掉，讓 LLM 老實說「不確定/需要更多資訊」，
   而不是硬牽一個不像的商品給客人。
2. 客人只有打字：目前還沒有文字版的語意搜尋（現在的 embeddings.py 只懂圖片，不懂文字），
   所以先用最土砲的關鍵字比對資料庫欄位頂著。之後如果換成 embeddings_clip.py
   （真正的 CLIP，同時懂圖片和文字），可以把這個函式整個換掉，main.py 完全不用動——
   跟 Phase 2 抽出 embeddings.py 是同一個設計理由。
"""

import re
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

import vector_store
from embeddings import encode_image
from models import Product

# 相似度低於這個門檻，就不當作「有信心的比對結果」交給 LLM，
# 避免明明長得不像，卻被 LLM 講得煞有其事。
MIN_IMAGE_SIMILARITY = 55.0

# 相似度雖然過了上面的門檻、但沒有到這個數字，Phase 5 會當作「有查到但不太確定」，
# 標記進低信心覆核佇列——比對得到不代表比對得準。
LOW_CONFIDENCE_IMAGE_SIMILARITY = 70.0

# 泛問（「你們都賣什麼」）或關鍵字完全比對不到時，用這幾個常數決定要推薦哪些商品。
HIGHLIGHT_LIMIT = 6
NEW_ARRIVAL_DAYS = 14
NEW_ARRIVAL_FALLBACK_COUNT = 5


def product_to_candidate(product: Product, similarity_percent: float | None = None) -> dict:
    candidate = {
        "product_id": product.id,
        "species": product.species,
        "morph_name": product.morph_name,
        "price": product.price,
        "stock_qty": product.stock_qty,
        "description": product.description,
        # 只給「有沒有照片」這個布林值，不把實際檔名/網址放進去餵給 LLM——
        # 圖片是 main.py 事後另外組好網址、直接回傳給前端顯示，不需要 LLM 知道路徑，
        # 這裡只是讓 LLM 講話時知道「這個商品有照片可以附上」。
        "has_photo": bool(product.photos),
    }
    if similarity_percent is not None:
        candidate["similarity_percent"] = round(similarity_percent, 1)
    return candidate


def find_candidates_by_image(
    db: Session, image_path: str, top_k: int = 3, min_similarity: float = MIN_IMAGE_SIMILARITY
) -> list[dict]:
    """回傳信心夠高、而且還在架上的候選商品（依相似度排序）。"""
    vector = encode_image(image_path)
    matches = vector_store.query_similar(vector, top_k=top_k)

    candidates = []
    for match in matches:
        product = (
            db.query(Product)
            .filter(Product.id == match["product_id"], Product.is_active == True)  # noqa: E712
            .first()
        )
        if product is None:
            continue

        similarity_percent = max(0.0, 1 - match["distance"] / 2) * 100
        if similarity_percent < min_similarity:
            continue

        candidates.append(product_to_candidate(product, similarity_percent))

    return candidates


def _split_words(query: str) -> set[str]:
    """用空白/標點拆出完整詞（客人如果打英文、數字，或剛好有分詞習慣時抓得到）。"""
    return {w for w in re.split(r"[\s,，。！？!?、]+", query) if w}


def _sliding_ngrams(query: str, sizes: tuple[int, ...] = (2, 3)) -> set[str]:
    """中文句子通常沒有空白分詞（「有沒有橘化的」是一整串），所以再補上 2~3 字的
    滑動視窗子字串當關鍵字候選——「有沒有橘化的」就會額外產生「橘化」等候選。
    """
    grams = set()
    for n in sizes:
        for i in range(len(query) - n + 1):
            grams.add(query[i : i + n])
    grams.discard("")
    return grams


def find_candidates_by_text(db: Session, query: str, limit: int = 5) -> list[dict]:
    """關鍵字搜尋：把客人打的句子拆出關鍵字候選，去對商品欄位比對。
    先用最土砲的 LIKE 比對，之後有文字版 CLIP（embeddings_clip.py 的 encode_text）
    可以整個換掉這個函式的內容就好，main.py 完全不用動。

    species（物種）欄位刻意只用「完整詞」去比對，不套用滑動視窗子字串——因為
    目前店裡只賣「豹紋守宮」，"守宮"（=gecko 的通稱）這種 2 字子字串幾乎每個
    商品都會中，拿它去比對 species 反而會把問其他物種（例如客人問「睫角守宮」）
    的客人也錯配到豹紋守宮上。morph_name/description 才是真正該用子字串比對的
    欄位（"橘化"、"雪花" 這種花紋名稱本來就短，而且夠具體）。
    """
    words = _split_words(query)
    ngrams = _sliding_ngrams(query)
    if not words and not ngrams:
        return []

    conditions = []
    for kw in words:  # 完整詞才比對 species（理由見上）
        conditions.append(Product.species.like(f"%{kw}%"))
    for kw in words | ngrams:  # 子字串也可以比對 morph_name/description
        like = f"%{kw}%"
        conditions.append(Product.morph_name.like(like))
        conditions.append(Product.description.like(like))

    products = (
        db.query(Product)
        .filter(Product.is_active == True, or_(*conditions))  # noqa: E712
        .order_by(Product.id.desc())
        .limit(limit)
        .all()
    )
    return [product_to_candidate(p) for p in products]


def list_category_options(db: Session) -> dict:
    """給聊天室畫「大分類」按鈕用：目前資料庫裡實際存在（上架中、有貨）的
    物種/顏色/花紋清單——按鈕永遠只會列出真的查得到東西的分類，不會有按了
    卻查無資料的空分類。"""
    products = db.query(Product).filter(Product.is_active == True, Product.stock_qty > 0).all()  # noqa: E712
    return {
        "species": sorted({p.species for p in products if p.species}),
        "colors": sorted({p.color for p in products if p.color}),
        "patterns": sorted({p.pattern for p in products if p.pattern}),
    }


def browse_products(db: Session, category: str, value: str | None = None, limit: int = 12) -> list[dict]:
    """大分類快速瀏覽用：直接查資料庫回傳商品，**不經過 LLM**——不花 token、
    幾乎瞬間回應，代價是沒有自然對話的語氣，前端要自己排版成商品卡片。

    刻意不回傳 price/stock_qty（跟 llm_reply.py 的規則一致：不主動報價、
    不透露確切庫存，客人要問了才透過 /chat 走 LLM 回答）。
    """
    base_query = db.query(Product).filter(Product.is_active == True, Product.stock_qty > 0)  # noqa: E712

    if category in ("species", "color", "pattern") and not value:
        return []

    if category == "species":
        products = base_query.filter(Product.species == value).order_by(Product.id.desc()).limit(limit).all()
    elif category == "color":
        products = base_query.filter(Product.color == value).order_by(Product.id.desc()).limit(limit).all()
    elif category == "pattern":
        products = base_query.filter(Product.pattern == value).order_by(Product.id.desc()).limit(limit).all()
    elif category == "featured":
        products = (
            base_query.filter(Product.is_featured == True)  # noqa: E712
            .order_by(Product.updated_at.desc())
            .limit(limit)
            .all()
        )
    elif category == "new_arrival":
        cutoff = datetime.utcnow() - timedelta(days=NEW_ARRIVAL_DAYS)
        products = base_query.filter(Product.created_at >= cutoff).order_by(Product.created_at.desc()).limit(limit).all()
        if not products:
            products = (
                base_query.order_by(Product.created_at.desc()).limit(min(limit, NEW_ARRIVAL_FALLBACK_COUNT)).all()
            )
    else:
        return []

    return [
        {
            "product_id": p.id,
            "species": p.species,
            "morph_name": p.morph_name,
            "color": p.color,
            "pattern": p.pattern,
            "description": p.description,
        }
        for p in products
    ]


def find_highlight_candidates(db: Session, limit: int = HIGHLIGHT_LIMIT) -> list[dict]:
    """客人問的話題太籠統（例如「你們都賣什麼」），或關鍵字剛好比對不到任何商品時，
    與其回傳空清單讓 LLM 只能說「沒查到」，不如改成推薦「精選」+「新進」商品，
    讓 LLM 有東西可以介紹——但一樣只挑資料庫裡真的存在、還在架上、還有庫存的商品，
    不違反「不能瞎掰」的規則。

    呼叫端要記得：這份清單不是「精準比對客人的問題」，是「店裡目前有什麼」，
    生成回覆時 (llm_reply.generate_reply) 要用 is_fallback=True 告訴 LLM 這件事。
    """
    base_query = db.query(Product).filter(Product.is_active == True, Product.stock_qty > 0)  # noqa: E712

    featured = (
        base_query.filter(Product.is_featured == True)  # noqa: E712
        .order_by(Product.updated_at.desc())
        .limit(limit)
        .all()
    )

    picked_ids = [p.id for p in featured]
    remaining = limit - len(featured)
    new_arrivals: list[Product] = []

    if remaining > 0:
        cutoff = datetime.utcnow() - timedelta(days=NEW_ARRIVAL_DAYS)
        query = base_query.filter(Product.created_at >= cutoff)
        if picked_ids:
            query = query.filter(Product.id.notin_(picked_ids))
        new_arrivals = query.order_by(Product.created_at.desc()).limit(remaining).all()

    # 最近 N 天內沒有新品，就退而求其次列最近上架的幾筆——
    # 不然「新進」這個話題在店剛開張、還沒累積新品時會整個是空的。
    if not new_arrivals and remaining > 0:
        query = base_query
        if picked_ids:
            query = query.filter(Product.id.notin_(picked_ids))
        new_arrivals = (
            query.order_by(Product.created_at.desc()).limit(min(remaining, NEW_ARRIVAL_FALLBACK_COUNT)).all()
        )

    results = []
    for p in featured:
        candidate = product_to_candidate(p)
        candidate["highlight_reason"] = "featured"
        results.append(candidate)
    for p in new_arrivals:
        candidate = product_to_candidate(p)
        candidate["highlight_reason"] = "new_arrival"
        results.append(candidate)

    return results
