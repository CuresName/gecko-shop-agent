"""
Phase 1 端對端驗證：新增商品(含照片)→列表→改庫存/價格→查單筆→上下架切換。
用 FastAPI 的 TestClient，不需要真的啟動伺服器。

⚠️ 這支腳本會產生測試資料，一定要跑在獨立的測試資料庫/測試照片目錄，
絕對不能動到 gecko_shop.db / static/uploads/ 這些正式資料——
所以在 import 任何專案程式之前，先把 DATABASE_URL 等環境變數指到測試專用的路徑。
"""

import io
import os
import shutil
import sys

TEST_DB_PATH = "test_gecko_shop.db"
TEST_UPLOAD_DIR = "static/_test_uploads"
TEST_CHROMA_PATH = "_test_chroma_store"

os.environ["DATABASE_URL"] = f"sqlite:///./{TEST_DB_PATH}"
os.environ["GECKO_SHOP_UPLOAD_DIR"] = TEST_UPLOAD_DIR
os.environ["GECKO_SHOP_CHROMA_PATH"] = TEST_CHROMA_PATH

# 這支腳本一律要跑在「沒有部署防護」的乾淨狀態，不管你的 .env 裡設了什麼——
# 不然 .env 一旦設了後台密碼/頻率限制/R2，TestClient 打的請求會被擋掉或跑去連 R2。
os.environ["ADMIN_USERNAME"] = ""
os.environ["ADMIN_PASSWORD"] = ""
os.environ["GECKO_SHOP_ENABLE_RATE_LIMIT"] = ""
os.environ["GECKO_SHOP_STORAGE_BACKEND"] = "local"


def _clean_test_artifacts():
    # Windows 上 SQLite 檔案被 SQLAlchemy 連線池占用時沒辦法直接刪，先把 engine 釋放掉
    try:
        from database import engine
        engine.dispose()
    except ImportError:
        pass

    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except PermissionError:
            print(f"[提醒] {TEST_DB_PATH} 目前還被占用，沒清掉也沒關係，下次執行前會自動重清一次")
    if os.path.exists(TEST_UPLOAD_DIR):
        shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)
    if os.path.exists(TEST_CHROMA_PATH):
        shutil.rmtree(TEST_CHROMA_PATH, ignore_errors=True)


# 每次驗證前先清掉「上一次測試」留下的資料，確保跑出來的結果是乾淨的
_clean_test_artifacts()

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

client = TestClient(app)


def fake_image(name="test.jpg"):
    return (name, io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg-bytes"), "image/jpeg")


def main():
    # 1. 新增商品（含兩張照片）
    resp = client.post(
        "/products",
        data={
            "species": "豹紋守宮",
            "morph_name": "Tangerine",
            "price": "1500",
            "stock_qty": "3",
            "description": "橘色系，個性溫馴，約 6 個月齡",
        },
        files=[("photos", fake_image("a.jpg")), ("photos", fake_image("b.jpg"))],
    )
    assert resp.status_code == 200, resp.text
    product = resp.json()
    assert product["species"] == "豹紋守宮"
    assert len(product["photos"]) == 2
    pid = product["id"]
    print(f"[OK] 新增商品成功，id={pid}，照片數={len(product['photos'])}")

    # 2. 列表要能看到剛新增的商品
    resp = client.get("/products")
    assert resp.status_code == 200
    products = resp.json()
    assert any(p["id"] == pid for p in products)
    print(f"[OK] 列表可見，共 {len(products)} 筆上架商品")

    # 3. 改庫存跟價格（後台表單的「儲存」按鈕做的事）
    resp = client.patch(f"/products/{pid}", json={"price": 1800, "stock_qty": 2})
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["price"] == 1800 and updated["stock_qty"] == 2
    print("[OK] 價格/庫存更新成功")

    # 4. 單筆查詢要能看到照片檔案確實存在
    resp = client.get(f"/products/{pid}")
    assert resp.status_code == 200
    detail = resp.json()
    for photo in detail["photos"]:
        assert os.path.exists(os.path.join(TEST_UPLOAD_DIR, photo["file_path"])), "照片檔案沒存到磁碟上"
    print(f"[OK] 照片檔案確實寫入 {TEST_UPLOAD_DIR}/（測試專用目錄，不會動到正式的 static/uploads/）")

    # 5. 下架後，預設列表看不到；include_inactive=true 才看得到
    resp = client.patch(f"/products/{pid}", json={"is_active": False})
    assert resp.status_code == 200

    resp = client.get("/products")
    assert not any(p["id"] == pid for p in resp.json())
    resp = client.get("/products?include_inactive=true")
    assert any(p["id"] == pid for p in resp.json())
    print("[OK] 下架後預設列表隱藏，include_inactive=true 仍可查到")

    # 6. 補上傳照片
    resp = client.post(f"/products/{pid}/photos", files=[("photos", fake_image("c.jpg"))])
    assert resp.status_code == 200
    assert len(resp.json()["photos"]) == 3
    print("[OK] 補上傳照片成功，現在共 3 張")

    print("\n全部通過 Phase 1（商品目錄 + 後台表單）可以收工了")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # 避免 Windows 主控台編碼印不出中文/符號時整個崩潰

    try:
        main()
    finally:
        _clean_test_artifacts()  # 不管測試成功與否，都不要在專案目錄留下測試垃圾
