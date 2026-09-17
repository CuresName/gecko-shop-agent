"""
一次性測試腳本：建立「5 種品種 × 7 色 × 5 花紋」的假商品，用來測試聊天室的
大分類快速瀏覽按鈕功能。

⚠️ 完全獨立於正式資料：用自己的 SQLite 檔案 (test_categories.db)、自己的照片
資料夾 (static/_test_category_uploads)，不會碰到 gecko_shop.db（本機真實 10 筆
商品）或 Railway 上的 Postgres/R2。照片是用 PIL 產生的純色色塊+文字，不是真的
守宮照片，一眼就看得出是測試資料。

用法：
    python seed_test_categories.py
接著用同一組環境變數啟動伺服器，就能在瀏覽器裡測試分類選單：
    DATABASE_URL=sqlite:///./test_categories.db GECKO_SHOP_UPLOAD_DIR=static/_test_category_uploads uvicorn main:app --reload
"""

import os

TEST_DB_PATH = "test_categories.db"
TEST_UPLOAD_DIR = "static/_test_category_uploads"

os.environ["DATABASE_URL"] = f"sqlite:///./{TEST_DB_PATH}"
os.environ["GECKO_SHOP_UPLOAD_DIR"] = TEST_UPLOAD_DIR
os.environ["GECKO_SHOP_STORAGE_BACKEND"] = "local"
os.environ["ADMIN_USERNAME"] = ""
os.environ["ADMIN_PASSWORD"] = ""

from database import Base, SessionLocal, engine  # noqa: E402
from models import Photo, Product  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

SPECIES = ["豹紋守宮", "睫角守宮", "麗紋守宮", "巨人守宮", "電光守宮"]
COLORS = ["紅", "橙", "黃", "綠", "藍", "靛", "紫"]
COLOR_RGB = {
    "紅": (211, 47, 47), "橙": (245, 124, 0), "黃": (251, 192, 45),
    "綠": (67, 160, 71), "藍": (30, 136, 229), "靛": (57, 73, 171), "紫": (142, 36, 170),
}
PATTERNS = ["條紋", "斑點", "素色", "漸層", "網紋"]

os.makedirs(TEST_UPLOAD_DIR, exist_ok=True)


def make_placeholder_image(path: str, species: str, color: str, pattern: str) -> None:
    rgb = COLOR_RGB[color]
    img = Image.new("RGB", (400, 400), color=rgb)
    draw = ImageDraw.Draw(img)
    label = f"[測試資料]\n{species}\n{color}系 / {pattern}"
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.multiline_text((20, 160), label, fill=(255, 255, 255), font=font, spacing=6)
    img.save(path, format="JPEG")


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(Product).count()
        if existing:
            print(f"test_categories.db 裡已經有 {existing} 筆商品了，避免重複建立，先手動清空再跑這支腳本。")
            return

        count = 0
        combo_index = 0
        for species in SPECIES:
            for _ in range(4):  # 每個品種建 4 筆，5 品種 = 20 筆，足夠覆蓋 7 色 + 5 花紋
                color = COLORS[combo_index % len(COLORS)]
                pattern = PATTERNS[combo_index % len(PATTERNS)]
                combo_index += 1

                product = Product(
                    species=species,
                    morph_name=f"{color}系{pattern} [測試]",
                    color=color,
                    pattern=pattern,
                    price=1000 + combo_index * 50,
                    stock_qty=1 + (combo_index % 3),
                    description=f"[測試資料] {species}，{color}色系搭配{pattern}花紋，僅供分類選單功能測試用。",
                    is_featured=(combo_index % 6 == 0),
                )
                db.add(product)
                db.flush()

                filename = f"TEST_{product.id}.jpg"
                make_placeholder_image(os.path.join(TEST_UPLOAD_DIR, filename), species, color, pattern)
                db.add(Photo(product_id=product.id, file_path=filename, is_embedded=False))
                count += 1

        db.commit()
        print(f"完成！建立了 {count} 筆測試商品（{len(SPECIES)} 品種 x 涵蓋 {len(COLORS)} 色 x {len(PATTERNS)} 花紋）。")
        print(f"資料庫：{TEST_DB_PATH}　照片：{TEST_UPLOAD_DIR}/")
    finally:
        db.close()


if __name__ == "__main__":
    main()
