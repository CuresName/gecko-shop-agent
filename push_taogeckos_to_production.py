"""
把本機 gecko_shop.db 裡「匯入但還沒上架」的 taogeckos 商品（product_type 是
gecko/accessory 且 is_active=False）推到正式站的 Postgres + R2。

刻意不在這裡算圖片特徵（CLIP embedding）——之前上線後同時開直播流量+跑
CLIP 就 OOM 重開機過一次，這裡只負責把資料/照片搬過去，embedding 留給
backfill_embeddings.py 在流量低的時段透過 railway ssh 另外補算，跟那次
事故之後採用的做法一致。

推上去的商品一樣維持 is_active=False（下架狀態）——上不上架、庫存數字
對不對，一樣要店家自己上正式站後台看過再按上架，跟本機那份「先審核」
的原則一致，不會因為換了環境就跳過這一步。

用法（跑之前要先把 Postgres Public Access 打開，且 .env 裡
MIGRATION_DATABASE_URL 是最新的那組 proxy 連線字串）：
    GECKO_SHOP_STORAGE_BACKEND=r2 python push_taogeckos_to_production.py
"""

import os

from dotenv import load_dotenv

load_dotenv()

if os.environ.get("GECKO_SHOP_STORAGE_BACKEND", "").lower() != "r2":
    raise SystemExit("要先設 GECKO_SHOP_STORAGE_BACKEND=r2 才能跑這支（照片要進 R2，不是本機硬碟）")

migration_url = os.environ.get("MIGRATION_DATABASE_URL")
if not migration_url:
    raise SystemExit("先在 .env 設 MIGRATION_DATABASE_URL（Postgres Public Access 開著時的 proxy 連線字串）")

import sqlalchemy
from sqlalchemy.orm import sessionmaker

import models
import storage
from database import Base

pg_url = migration_url
if pg_url.startswith("postgresql://") or pg_url.startswith("postgres://"):
    pg_url = pg_url.split("://", 1)[1]
    pg_url = f"postgresql+psycopg://{pg_url}"

pg_engine = sqlalchemy.create_engine(pg_url)
PgSession = sessionmaker(bind=pg_engine)

# 本機 DB 直接用 sqlite3，不透過 database.py（避免跟上面的 Postgres engine 搶同一個 SessionLocal）
import sqlite3

local_conn = sqlite3.connect("gecko_shop.db")
local_conn.row_factory = sqlite3.Row


def main() -> None:
    Base.metadata.create_all(bind=pg_engine)

    with pg_engine.connect() as conn:
        cols = [r[0] for r in conn.execute(sqlalchemy.text("SELECT column_name FROM information_schema.columns WHERE table_name='products'"))]
    if "product_type" not in cols:
        raise SystemExit("正式站 Postgres 的 products 表還沒有 product_type 欄位，先跑 schema migration（ALTER TABLE）")

    pending = local_conn.execute(
        "SELECT * FROM products WHERE is_active = 0 ORDER BY id"
    ).fetchall()
    print(f"本機待上架商品：{len(pending)} 筆，開始推到正式站...")

    pg = PgSession()
    pushed = 0
    try:
        for row in pending:
            product = models.Product(
                product_type=row["product_type"],
                species=row["species"],
                morph_name=row["morph_name"],
                color=row["color"] or "",
                pattern=row["pattern"] or "",
                price=row["price"],
                stock_qty=row["stock_qty"],
                description=row["description"] or "",
                is_active=False,
                is_featured=False,
            )
            pg.add(product)
            pg.flush()

            photos = local_conn.execute(
                "SELECT * FROM photos WHERE product_id = ?", (row["id"],)
            ).fetchall()
            for photo in photos:
                data = storage_read_local(photo["file_path"])
                if data is None:
                    print(f"  [警告] 本機照片讀不到：{photo['file_path']}，跳過這張")
                    continue
                storage.save("uploads", photo["file_path"], data)  # 這裡的 storage 是 r2 模式，寫進 R2
                pg.add(models.Photo(product_id=product.id, file_path=photo["file_path"], is_embedded=False))

            pg.commit()
            pushed += 1
            print(f"  已推送 {row['species']} / {row['morph_name']}（正式站 id={product.id}）")
    finally:
        pg.close()

    print(f"\n完成！推送 {pushed} 筆商品到正式站（全部是下架狀態，等你上後台確認）。")
    print("之後找一個流量低的時段，透過 railway ssh 跑 backfill_embeddings.py 補圖片特徵。")


def storage_read_local(file_path: str) -> bytes | None:
    local_path = os.path.join("static", "uploads", file_path)
    if not os.path.exists(local_path):
        return None
    with open(local_path, "rb") as f:
        return f.read()


if __name__ == "__main__":
    main()
