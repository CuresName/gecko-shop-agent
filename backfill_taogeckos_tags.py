"""
一次性補丁：import_taogeckos.py 第一次跑的時候忘了 load_dotenv()，
導致 ANTHROPIC_API_KEY 讀不到、所有守宮商品的 color/pattern/AI 描述都補標籤失敗
（有 try/except 接住，所以照片/價格都正常存進去了，只有這步靜默失敗變空字串）。

這支只補標籤，不重新下載照片、不重新算圖片特徵——只挑 product_type=gecko 且
color 是空字串的商品，用它已經存好的照片重跑一次 AI 補標籤。
"""

from dotenv import load_dotenv

load_dotenv()

from database import SessionLocal
import models
import photo_tagging
import storage

db = SessionLocal()
try:
    products = (
        db.query(models.Product)
        .filter(models.Product.product_type == "gecko", models.Product.color == "")
        .all()
    )
    print(f"要補標籤的守宮商品：{len(products)} 筆")

    for p in products:
        if not p.photos:
            print(f"  [跳過] {p.id} {p.morph_name} 沒有照片")
            continue
        image_bytes = storage.read("uploads", p.photos[0].file_path)
        try:
            tags = photo_tagging.suggest_color_pattern(image_bytes, p.species, p.morph_name)
        except Exception as e:
            print(f"  [失敗] {p.id} {p.morph_name}: {e}")
            continue

        p.color = tags.get("color", "")
        p.pattern = tags.get("pattern", "")
        if tags.get("description"):
            source_tag = p.description.split("（資料來源")[-1]
            p.description = tags["description"] + "（資料來源" + source_tag
        db.commit()
        print(f"  已補 {p.id} {p.morph_name} -> color={p.color} pattern={p.pattern}")

    print("完成！")
finally:
    db.close()
