"""
把 scrape_taogeckos.py 抓好的 manifest.json 匯入本機正式資料庫（gecko_shop.db）。

刻意全部先用 is_active=False（下架狀態）匯入——不是不信任資料本身，是價格/圖片
雖然來自真實型錄，但庫存數字（尤其周邊用品）型錄上看不到，用猜的違反這個專案
「不能亂報庫存」的核心原則。匯入後要進後台「商品清單」逐筆看過、確認庫存數字、
按「上架」才會出現在客人看得到的聊天室裡——這是先前跟店家確認過的做法。

守宮商品：庫存先設 1（型錄上一筆＝一隻活體，符合現貨活體零售的常態），
          顏色/花紋/介紹交給 Claude 看照片補（物種/商品名稱已經從型錄拿到了，
          不用重新用 AI 猜，省 token）。
周邊用品：庫存先設 1（型錄看不到實際庫存，用最保守的預設值，逼店家上架前要
          自己確認實際庫存），不叫 AI（型錄上的品名/分類已經夠用，不需要看照片猜）。

用法：
    python import_taogeckos.py
"""

import json
import os
import tempfile

from dotenv import load_dotenv

load_dotenv()

from database import Base, SessionLocal, engine
import models
import photo_tagging
import storage
import vector_store
from embeddings import encode_image

STAGING_DIR = "scrape_staging"
IMAGES_DIR = os.path.join(STAGING_DIR, "images")

# 型錄品名開頭常有內部編號（"01 "、"02 "），對客人沒有意義，匯入前先去掉。
import re


def clean_name(raw_name: str) -> str:
    return re.sub(r"^\d+\s*", "", raw_name).strip()


def import_gecko(db, entry: dict) -> models.Product:
    morph_name = clean_name(entry["raw_name"])
    first_image_path = os.path.join(IMAGES_DIR, entry["local_images"][0])
    with open(first_image_path, "rb") as f:
        image_bytes = f.read()

    try:
        tags = photo_tagging.suggest_color_pattern(image_bytes, entry["species"], morph_name)
    except Exception as e:
        print(f"  [警告] AI 補標籤失敗（{morph_name}）：{e}，color/pattern 先留空")
        tags = {"color": "", "pattern": "", "description": ""}

    description = tags.get("description") or f"{entry['species']}，{morph_name}。"
    description += f"（資料來源：taogeckos.com，商品型錄 #{entry['source_id']}）"

    product = models.Product(
        product_type="gecko",
        species=entry["species"],
        morph_name=morph_name,
        color=tags.get("color", ""),
        pattern=tags.get("pattern", ""),
        price=entry["price"],
        stock_qty=1,
        description=description,
        is_active=False,  # 上架前要先在後台確認庫存
    )
    db.add(product)
    db.flush()

    for filename in entry["local_images"]:
        _attach_photo(db, product.id, os.path.join(IMAGES_DIR, filename), embed=True)

    return product


def import_accessory(db, entry: dict) -> models.Product:
    name = clean_name(entry["raw_name"])
    description = f"{entry['species']}類周邊用品。（資料來源：taogeckos.com，商品型錄 #{entry['source_id']}）"

    product = models.Product(
        product_type="accessory",
        species=entry["species"],  # 借用 species 放用品分類
        morph_name=name,
        color="",
        pattern="",
        price=entry["price"],
        stock_qty=1,
        description=description,
        is_active=False,
    )
    db.add(product)
    db.flush()

    for filename in entry["local_images"]:
        _attach_photo(db, product.id, os.path.join(IMAGES_DIR, filename), embed=False)

    return product


def _attach_photo(db, product_id: int, local_path: str, embed: bool) -> None:
    with open(local_path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(local_path)[1] or ".jpg"
    saved_name = f"tao_{os.path.basename(local_path)}"
    storage.save("uploads", saved_name, data)

    db_photo = models.Photo(product_id=product_id, file_path=saved_name)

    if embed:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                vector = encode_image(tmp_path)
            finally:
                os.remove(tmp_path)
            db.add(db_photo)
            db.flush()
            vector_store.add_photo(
                photo_id=db_photo.id, product_id=product_id, vector=vector, extra_metadata={"file_path": saved_name}
            )
            db_photo.is_embedded = True
        except Exception as e:
            print(f"  [警告] 照片算特徵失敗（{local_path}）：{e}")
            db.add(db_photo)
    else:
        db.add(db_photo)


def main() -> None:
    Base.metadata.create_all(bind=engine)

    manifest_path = os.path.join(STAGING_DIR, "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    db = SessionLocal()
    try:
        gecko_count = 0
        accessory_count = 0
        for entry in manifest:
            if not entry.get("local_images"):
                print(f"[跳過] 沒有下載到照片：{entry['raw_name']}")
                continue
            if entry["product_type"] == "gecko":
                import_gecko(db, entry)
                gecko_count += 1
            else:
                import_accessory(db, entry)
                accessory_count += 1
            db.commit()
            print(f"  已匯入 {entry['species']} / {clean_name(entry['raw_name'])}")

        print(f"\n完成！匯入 {gecko_count} 筆守宮 + {accessory_count} 筆周邊用品，全部是「下架」狀態。")
        print("進 /admin.html 的「商品清單」逐筆看過、確認庫存數字後再按「上架」。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
