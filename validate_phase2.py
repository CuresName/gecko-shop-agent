"""
Phase 2 驗證：
  1. 用商品自己的照片當作「客人上傳的照片」去查，應該要排第一名比對到自己
  2. 用同色系（橘化）的照片去查，排前面的應該也偏向同色系，而不是雪花/淡色系
     （這是色彩直方圖能不能抓到 morph 差異的基本檢查）
"""

import os
import sys

# 跟 validate_phase1.py 一樣：這支腳本要跑在沒有部署防護的乾淨狀態，不管 .env 裡
# 設了什麼後台密碼/頻率限制，不然 /search/image 這種受保護的端點會被擋掉。
os.environ["ADMIN_USERNAME"] = ""
os.environ["ADMIN_PASSWORD"] = ""
os.environ["GECKO_SHOP_ENABLE_RATE_LIMIT"] = ""

from fastapi.testclient import TestClient

from main import app
import models
from database import SessionLocal

client = TestClient(app)


def main():
    db = SessionLocal()
    photos = db.query(models.Photo).all()
    photo_by_id = {p.id: p for p in photos}
    product_by_id = {p.id: p for p in db.query(models.Product).all()}
    db.close()

    print(f"共 {len(photos)} 張照片可測試\n")

    # 1. 自己比對自己，應該是第一名 + 相似度很高
    self_match_ok = 0
    for photo in photos:
        path = f"static/uploads/{photo.file_path}"
        with open(path, "rb") as f:
            resp = client.post("/search/image", files={"photo": (photo.file_path, f, "image/jpeg")}, params={"top_k": 3})
        results = resp.json()["results"]
        top1 = results[0] if results else None
        is_self = top1 and top1["product"]["id"] == photo.product_id
        if is_self:
            self_match_ok += 1
        product = product_by_id[photo.product_id]
        status = "OK" if is_self else "MISS"
        top1_sim = top1["similarity_percent"] if top1 else "-"
        print(f"[{status:4s}] 商品#{photo.product_id} ({product.morph_name})  自我比對相似度={top1_sim}%")

    print(f"\n自我比對正確率：{self_match_ok}/{len(photos)}")

    # 2. 找一張橘化(Tangerine)的照片，看前幾名是不是也偏橘化，而不是雪花系
    tangerine_photo = next(
        (p for p in photos if "橘化" in product_by_id[p.product_id].morph_name), None
    )
    if tangerine_photo:
        path = f"static/uploads/{tangerine_photo.file_path}"
        with open(path, "rb") as f:
            resp = client.post("/search/image", files={"photo": (tangerine_photo.file_path, f, "image/jpeg")}, params={"top_k": 5})
        results = resp.json()["results"]
        print(f"\n用「{product_by_id[tangerine_photo.product_id].morph_name}」照片查詢，前 {len(results)} 名：")
        for r in results:
            p = r["product"]
            print(f"  {r['similarity_percent']:5.1f}%  {p['species']} / {p['morph_name']}")

    print("\n完成 ✅ Phase 2（影像特徵 + 向量搜尋）基本可用")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # 避免 Windows 主控台編碼印不出中文/符號時整個崩潰
    main()
