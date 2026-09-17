"""
第 0 層：商品目錄 API + 後台表單
================================
這一層跑通之後，第 2 階段（影像特徵/向量搜尋）跟第 3 階段（LLM 回覆）
才有真正的資料可以查——所以先把「新增商品、上傳照片、改庫存/價格」這幾件事做穩。

啟動方式：
    uvicorn main:app --reload
然後打開 http://127.0.0.1:8000/admin.html 使用後台表單。
"""

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import catalog_search
import models
import photo_tagging
import schemas
import storage
import vector_store
from database import Base, SessionLocal, engine, get_db
from embeddings import encode_image
from llm_reply import generate_reply
from security import AdminBasicAuthMiddleware, ChatRateLimitMiddleware

Base.metadata.create_all(bind=engine)

app = FastAPI(title="守宮商品目錄 API")
app.add_middleware(ChatRateLimitMiddleware)
app.add_middleware(AdminBasicAuthMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def chat_page():
    """首頁就是客人看到的聊天室——這個專案本身就是網站，不是另外接在哪個頁面裡的小工具。"""
    from fastapi.responses import FileResponse

    return FileResponse("static/index.html")


@app.get("/admin.html")
def admin_page():
    from fastapi.responses import FileResponse

    return FileResponse("static/admin.html")


_MEDIA_NAMESPACES = {"uploads", "chat_log_photos"}


@app.get("/media/{namespace}/{filename}")
def get_media(namespace: str, filename: str):
    """後台看照片（商品照片/低信心對話照片）都走這個端點，不管照片實際存在本機
    還是 R2，網址格式都一樣——這條路徑不在 PUBLIC_PATHS 裡，所以部署後一樣要
    後台密碼才能看，客人看不到。
    """
    from fastapi.responses import Response

    if namespace not in _MEDIA_NAMESPACES:
        raise HTTPException(status_code=404, detail="找不到這個檔案")
    try:
        data = storage.read(namespace, filename)
    except Exception:
        raise HTTPException(status_code=404, detail="找不到這個檔案")

    import mimetypes

    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(content=data, media_type=media_type)


def _save_photo_and_embed(product_id: int, photo: UploadFile, db: Session) -> models.Photo | None:
    """存檔（本機或 R2，由 storage.py 決定）+ 算特徵 + 存進 Chroma + 建立 Photo 紀錄。"""
    if not photo.filename:
        return None

    ext = os.path.splitext(photo.filename)[1]
    saved_name = f"{uuid.uuid4().hex}{ext}"
    data = photo.file.read()
    storage.save("uploads", saved_name, data)

    db_photo = models.Photo(product_id=product_id, file_path=saved_name)

    try:
        # encode_image 需要一個本機檔案路徑，不管照片實際存在哪裡（本機或 R2），
        # 都先寫一份到暫存檔給它讀，跟客人上傳照片查詢時的做法一致。
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            vector = encode_image(tmp_path)
        finally:
            os.remove(tmp_path)

        db.add(db_photo)
        db.flush()  # 先拿到 db_photo.id，才能存進 Chroma 當 key
        vector_store.add_photo(
            photo_id=db_photo.id,
            product_id=product_id,
            vector=vector,
            extra_metadata={"file_path": saved_name},
        )
        db_photo.is_embedded = True
    except Exception as e:  # 算特徵失敗不影響照片本身存檔，留給 backfill_embeddings.py 之後補算
        print(f"[警告] 照片 {saved_name} 算特徵失敗：{e}，先跳過，之後可用 backfill_embeddings.py 補算")
        db.add(db_photo)

    return db_photo


@app.post("/products", response_model=schemas.ProductOut)
def create_product(
    species: str = Form(...),
    morph_name: str = Form(...),
    price: float = Form(...),
    stock_qty: int = Form(0),
    description: str = Form(""),
    is_featured: bool = Form(False),
    photos: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    """新增一筆商品，可以同時上傳多張照片（後台表單就是呼叫這個），照片會立刻算好影像特徵。"""
    product = models.Product(
        species=species,
        morph_name=morph_name,
        price=price,
        stock_qty=stock_qty,
        description=description,
        is_featured=is_featured,
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    for photo in photos:
        _save_photo_and_embed(product.id, photo, db)

    db.commit()
    db.refresh(product)
    return product


@app.get("/products", response_model=list[schemas.ProductOut])
def list_products(include_inactive: bool = False, db: Session = Depends(get_db)):
    query = db.query(models.Product)
    if not include_inactive:
        query = query.filter(models.Product.is_active == True)  # noqa: E712
    return query.order_by(models.Product.id.desc()).all()


@app.get("/products/{product_id}", response_model=schemas.ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="找不到這個商品")
    return product


@app.patch("/products/{product_id}", response_model=schemas.ProductOut)
def update_product(product_id: int, payload: schemas.ProductUpdate, db: Session = Depends(get_db)):
    """改庫存/價格用這個 —— 只送你想改的欄位就好。"""
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="找不到這個商品")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return product


@app.post("/products/{product_id}/photos", response_model=schemas.ProductOut)
def add_photos(product_id: int, photos: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    """商品建立後，之後想再補照片用這個，一樣會立刻算好影像特徵。"""
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="找不到這個商品")

    for photo in photos:
        _save_photo_and_embed(product.id, photo, db)

    db.commit()
    db.refresh(product)
    return product


@app.post("/products/suggest_tags")
def suggest_tags(photo: UploadFile = File(...)):
    """後台新增商品時用：上傳一張照片，讓 Claude 幫忙猜物種/樣式/描述，
    回傳的是建議值，不會存進資料庫——後台表單會把這些值先填進欄位，你送出前還是要自己看過確認。
    """
    image_bytes = photo.file.read()
    media_type = photo.content_type or "image/jpeg"

    try:
        suggestion = photo_tagging.suggest_tags(image_bytes, media_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 標籤建議失敗：{e}")

    return suggestion


@app.post("/products/suggest_tags_batch")
def suggest_tags_batch(photos: list[UploadFile] = File(...)):
    """批量新增用：一次丟好幾張照片，逐張讓 Claude 猜物種/樣式/描述。

    回傳順序跟上傳順序一致（前端才能對得回哪張照片是哪筆建議），單張猜失敗
    不會讓整批中斷——那張的 `error` 欄位會有錯誤訊息，其他張照樣正常回傳。
    """
    results = []
    for photo in photos:
        image_bytes = photo.file.read()
        media_type = photo.content_type or "image/jpeg"
        try:
            suggestion = photo_tagging.suggest_tags(image_bytes, media_type)
            suggestion["filename"] = photo.filename
            suggestion["error"] = None
        except Exception as e:
            suggestion = {
                "filename": photo.filename,
                "species": "",
                "morph_name": "",
                "description": "",
                "confidence_note": "",
                "error": f"這張猜標籤失敗：{e}",
            }
        results.append(suggestion)

    return {"suggestions": results}


@app.post("/search/image")
def search_by_image(photo: UploadFile = File(...), top_k: int = 5, db: Session = Depends(get_db)):
    """客人上傳一張照片，回傳最像的前幾個商品（含相似度）。這是聊天機器人之後要呼叫的核心比對 API。"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(photo.filename or "")[1] or ".jpg") as tmp:
        shutil.copyfileobj(photo.file, tmp)
        tmp_path = tmp.name

    try:
        query_vector = encode_image(tmp_path)
    finally:
        os.remove(tmp_path)

    matches = vector_store.query_similar(query_vector, top_k=top_k)

    results = []
    for m in matches:
        product = db.query(models.Product).filter(models.Product.id == m["product_id"]).first()
        if not product:
            continue
        # distance 是 cosine distance（0 = 完全一樣，越大越不像），轉成比較直覺的相似度百分比
        similarity = max(0.0, 1 - m["distance"] / 2) * 100
        results.append(
            {
                "product": schemas.ProductOut.model_validate(product).model_dump(mode="json"),
                "matched_photo_id": m["photo_id"],
                "similarity_percent": round(similarity, 1),
            }
        )

    return {"query_photo": photo.filename, "results": results}


MAX_HISTORY_TURNS = 10  # 前端會把最近幾輪對話一起送過來，這裡設個上限避免無限累積、拖垮 token 用量

LOG_PHOTO_RETENTION_DAYS = 90  # 只有低信心的對話才會留客人照片，而且只留這麼久（cleanup_chat_logs.py 負責清）


@app.post("/chat")
def chat(
    message: str = Form(""),
    photo: Optional[UploadFile] = File(None),
    history: str = Form("[]"),
    db: Session = Depends(get_db),
):
    """客人問客服的地方：可以純打字，也可以附一張照片（或兩個都有）。

    流程：先從資料庫查出「真的存在、而且信心夠高」的候選商品（有照片就用影像比對，
    沒照片就用關鍵字搜尋），再交給 LLM 組成自然的回覆——LLM 不能提到候選清單以外
    的商品/價格/庫存，回傳的 candidates 欄位就是這次回覆實際依據的資料。

    history：前端（客人瀏覽器）保留的這個分頁最近幾輪對話，讓 LLM 聽得懂「這隻」
    「剛剛那個」這種接續問法——是每個客人自己瀏覽器裡的東西，不同客人不會共用到。

    Phase 5：每一通都會存一筆 ChatLog（文字紀錄，方便事後檢查系統講了什麼）。
    只有「低信心」的對話（照片比對不到/比對得不夠準，或文字關鍵字完全查不到）才會
    額外留一份客人上傳的原始照片，並設 90 天保留期限——一般對話不留照片。
    """
    candidates: list[dict] = []
    is_fallback = False
    is_low_confidence = False
    low_confidence_reason = ""
    had_photo = photo is not None and bool(photo.filename)
    photo_bytes: bytes | None = None
    photo_suffix = ".jpg"

    try:
        parsed_history = json.loads(history)
        if not isinstance(parsed_history, list):
            parsed_history = []
    except (json.JSONDecodeError, TypeError):
        parsed_history = []
    parsed_history = parsed_history[-MAX_HISTORY_TURNS:]

    if had_photo:
        photo_suffix = os.path.splitext(photo.filename)[1] or ".jpg"
        photo_bytes = photo.file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=photo_suffix) as tmp:
            tmp.write(photo_bytes)
            tmp_path = tmp.name
        try:
            candidates = catalog_search.find_candidates_by_image(db, tmp_path)
        finally:
            os.remove(tmp_path)

        if not candidates:
            is_low_confidence = True
            low_confidence_reason = "照片比對不到任何有信心的候選商品"
        elif candidates[0]["similarity_percent"] < catalog_search.LOW_CONFIDENCE_IMAGE_SIMILARITY:
            is_low_confidence = True
            low_confidence_reason = f"照片比對到商品，但相似度只有 {candidates[0]['similarity_percent']}%，不算有把握"
    elif message.strip():
        candidates = catalog_search.find_candidates_by_text(db, message)
        if not candidates:
            # 關鍵字完全比對不到（可能是泛問「你們都賣什麼」，也可能問的東西剛好沒有）——
            # 改成推薦精選/新進商品，而不是讓 LLM 只能回「沒查到」。
            candidates = catalog_search.find_highlight_candidates(db)
            is_fallback = bool(candidates)
            is_low_confidence = True
            low_confidence_reason = "文字關鍵字完全比對不到任何商品，改用精選/新進 fallback"

    reply = generate_reply(message, candidates, is_fallback=is_fallback, history=parsed_history)

    log_photo_path = None
    photo_expires_at = None
    if is_low_confidence and photo_bytes:
        log_photo_path = f"{uuid.uuid4().hex}{photo_suffix}"
        storage.save("chat_log_photos", log_photo_path, photo_bytes)
        photo_expires_at = datetime.utcnow() + timedelta(days=LOG_PHOTO_RETENTION_DAYS)

    log_entry = models.ChatLog(
        customer_message=message,
        reply=reply,
        candidates_json=json.dumps(candidates, ensure_ascii=False),
        is_fallback=is_fallback,
        is_low_confidence=is_low_confidence,
        low_confidence_reason=low_confidence_reason,
        had_photo=had_photo,
        photo_path=log_photo_path,
        photo_expires_at=photo_expires_at,
    )
    db.add(log_entry)
    db.commit()

    return {"reply": reply, "candidates": candidates, "is_fallback": is_fallback}


@app.get("/chat_logs")
def list_chat_logs(low_confidence_only: bool = False, include_reviewed: bool = False, limit: int = 100, db: Session = Depends(get_db)):
    """Phase 5 覆核佇列用：預設只回傳「低信心、還沒標記已看過」的對話，最新的排前面。"""
    query = db.query(models.ChatLog)
    if low_confidence_only:
        query = query.filter(models.ChatLog.is_low_confidence == True)  # noqa: E712
    if not include_reviewed:
        query = query.filter(models.ChatLog.reviewed == False)  # noqa: E712

    logs = query.order_by(models.ChatLog.id.desc()).limit(limit).all()
    return [
        {
            "id": log.id,
            "customer_message": log.customer_message,
            "reply": log.reply,
            "candidates": json.loads(log.candidates_json),
            "is_fallback": log.is_fallback,
            "is_low_confidence": log.is_low_confidence,
            "low_confidence_reason": log.low_confidence_reason,
            "had_photo": log.had_photo,
            "photo_path": log.photo_path,
            "reviewed": log.reviewed,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


@app.patch("/chat_logs/{log_id}")
def update_chat_log(log_id: int, reviewed: bool = True, db: Session = Depends(get_db)):
    """在覆核佇列裡按「標記已看過」用——只是把這筆從佇列裡拿掉，不會刪除紀錄本身。"""
    log = db.query(models.ChatLog).filter(models.ChatLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="找不到這筆紀錄")
    log.reviewed = reviewed
    db.commit()
    return {"id": log.id, "reviewed": log.reviewed}
