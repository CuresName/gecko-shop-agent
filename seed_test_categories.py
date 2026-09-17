"""
一次性測試腳本：建立「5 種品種 × 7 色 × 5 花紋」的假商品，用來測試聊天室的
大分類快速瀏覽按鈕功能。

⚠️ 完全獨立於正式資料：用自己的 SQLite 檔案 (test_categories.db)、自己的照片
資料夾 (static/_test_category_uploads)，不會碰到 gecko_shop.db（本機真實 10 筆
商品）或 Railway 上的 Postgres/R2。

照片是輪流借用 static/uploads/ 裡你自己真實的 10 張商品照片、疊上一層半透明的
「[測試資料]」文字標籤——不是去網路上抓別人的照片來用（版權問題），也不是純色
色塊（太醜、測起來不像真的），而是用你自己本來就有權使用的照片，加上清楚的
文字標記，一眼就看得出是測試資料、也不會跟真實商品的花色描述搞混。

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
PATTERNS = ["條紋", "斑點", "素色", "漸層", "網紋"]

REAL_UPLOAD_DIR = "static/uploads"  # 借用這裡面你自己真實的商品照片當底圖

os.makedirs(TEST_UPLOAD_DIR, exist_ok=True)


def make_placeholder_image(path: str, source_photo: str, species: str, color: str, pattern: str) -> None:
    """借用一張你自己的真實商品照片當底圖，疊上半透明色塊+文字標籤，
    清楚標記「這是測試資料，不是這張照片真正的商品內容」。
    """
    base = Image.open(source_photo).convert("RGB").resize((400, 400))

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, 300, 400, 400], fill=(0, 0, 0, 160))  # 底部半透明黑條，文字比較好讀

    label = f"[測試資料] {species}\n{color}系 / {pattern}"
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.multiline_text((12, 310), label, fill=(255, 255, 255, 255), font=font, spacing=4)

    composed = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    composed.save(path, format="JPEG")


def main() -> None:
    Base.metadata.create_all(bind=engine)

    real_photos = sorted(
        f for f in os.listdir(REAL_UPLOAD_DIR)
        if f.lower().endswith((".jpg", ".jpeg", ".png")) and not f.startswith("TEST_")
    )
    if not real_photos:
        print(f"[錯誤] {REAL_UPLOAD_DIR}/ 裡找不到任何照片可以借用，先確認真實商品照片還在。")
        return

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
                source_photo = os.path.join(REAL_UPLOAD_DIR, real_photos[combo_index % len(real_photos)])
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
                make_placeholder_image(os.path.join(TEST_UPLOAD_DIR, filename), source_photo, species, color, pattern)
                db.add(Photo(product_id=product.id, file_path=filename, is_embedded=False))
                count += 1

        db.commit()
        print(f"完成！建立了 {count} 筆測試商品（{len(SPECIES)} 品種 x 涵蓋 {len(COLORS)} 色 x {len(PATTERNS)} 花紋）。")
        print(f"資料庫：{TEST_DB_PATH}　照片：{TEST_UPLOAD_DIR}/（借用 {REAL_UPLOAD_DIR}/ 的真實照片當底圖）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
