"""
一次性遷移腳本：把本機 SQLite 裡現有的商品資料 + 照片，搬到 Postgres + Cloudflare R2。
=================================================================================
只在正式要切換到 Railway 部署時跑一次，不是重複執行的工具——如果 Postgres 裡已經
有商品資料了，會直接中止，避免重複匯入。

執行方式（兩個條件都要滿足）：
    GECKO_SHOP_STORAGE_BACKEND=r2 python migrate_to_postgres.py

需要 .env 裡已經有：MIGRATION_DATABASE_URL、R2_ACCESS_KEY_ID、R2_SECRET_ACCESS_KEY、
R2_ENDPOINT_URL、R2_BUCKET_NAME（這些是 scripts/setup_deployment.sh 那個精靈跑完
就會存好的）。

刻意不搬的東西：
  - ChatLog（Phase 5 的對話紀錄）——這是營運過程中累積的紀錄，不是「商品目錄」的
    一部分，正式上線後重新開始累積是合理的，沒有必要把本機測試時的對話紀錄也搬過去。
  - Chroma 向量索引——本機的向量是本機算的，跟 R2 上的照片檔案綁在一起才有意義，
    搬完之後要在 Railway 那邊用同樣的 GECKO_SHOP_STORAGE_BACKEND=r2 重新跑一次
    backfill_embeddings.py 才會有正確的向量索引（所以這裡新建的 Photo 都是
    is_embedded=False）。
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

MIGRATION_DATABASE_URL = os.environ.get("MIGRATION_DATABASE_URL")
if not MIGRATION_DATABASE_URL:
    print("[錯誤] .env 裡沒有 MIGRATION_DATABASE_URL，先確認 scripts/setup_deployment.sh 有跑完。")
    sys.exit(1)

import storage  # noqa: E402

if not storage.USE_R2:
    print("[錯誤] 這支腳本要在 GECKO_SHOP_STORAGE_BACKEND=r2 的狀態下執行，不然照片會存到本機而不是 R2。")
    print("請用：GECKO_SHOP_STORAGE_BACKEND=r2 python migrate_to_postgres.py")
    sys.exit(1)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import models  # noqa: E402
from database import Base  # noqa: E402
from database import SessionLocal as LocalSessionLocal  # 本機 SQLite（沒設 DATABASE_URL 就是預設值）

pg_url = MIGRATION_DATABASE_URL
if pg_url.startswith("postgresql://") or pg_url.startswith("postgres://"):
    pg_url = f"postgresql+psycopg://{pg_url.split('://', 1)[1]}"

pg_engine = create_engine(pg_url)
PgSessionLocal = sessionmaker(bind=pg_engine)


def main() -> None:
    Base.metadata.create_all(bind=pg_engine)
    print("[OK] Postgres 資料表已建立（或本來就存在）")

    local_db = LocalSessionLocal()
    pg_db = PgSessionLocal()

    try:
        existing = pg_db.query(models.Product).count()
        if existing:
            print(f"[中止] Postgres 裡已經有 {existing} 筆商品了，避免重複匯入，先手動清空再跑這支腳本。")
            return

        products = local_db.query(models.Product).order_by(models.Product.id).all()
        print(f"本機共有 {len(products)} 筆商品，開始搬移...\n")

        migrated_photos = 0
        for product in products:
            new_product = models.Product(
                species=product.species,
                morph_name=product.morph_name,
                price=product.price,
                stock_qty=product.stock_qty,
                description=product.description,
                is_active=product.is_active,
                is_featured=product.is_featured,
                created_at=product.created_at,
                updated_at=product.updated_at,
            )
            pg_db.add(new_product)
            pg_db.flush()  # 拿到新的 id，才能建立對應的 Photo

            for photo in product.photos:
                local_path = os.path.join("static/uploads", photo.file_path)
                if not os.path.exists(local_path):
                    print(f"  [警告] 找不到照片檔案 {local_path}，跳過（商品：{product.morph_name}）")
                    continue

                with open(local_path, "rb") as f:
                    data = f.read()
                storage.save("uploads", photo.file_path, data)

                pg_db.add(
                    models.Photo(
                        product_id=new_product.id,
                        file_path=photo.file_path,
                        is_embedded=False,
                        created_at=photo.created_at,
                    )
                )
                migrated_photos += 1
                print(f"  [OK] {product.morph_name} 的照片 {photo.file_path} 已上傳到 R2")

        pg_db.commit()
        print(f"\n完成！共搬了 {len(products)} 筆商品、{migrated_photos} 張照片到 Postgres + R2。")
        print("下一步：部署到 Railway 之後，記得用同樣的 GECKO_SHOP_STORAGE_BACKEND=r2")
        print("跑一次 backfill_embeddings.py，把圖片比對用的向量索引在 Railway 上重新算過。")
    finally:
        local_db.close()
        pg_db.close()


if __name__ == "__main__":
    main()
