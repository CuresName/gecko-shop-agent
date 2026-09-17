"""
Phase 3 驗證腳本
=================
重點驗證「LLM 不能自己編東西」這件事的前半段——資料查詢跟防呆邏輯是不是真的
把好關：
    1. 拿照片查 -> 只回傳信心夠高、而且還在架上的商品，不該回傳的不會混進來。
    2. 純打字查 -> 關鍵字搜尋抓得到明顯相關的商品，抓不到的話回傳空清單
       （空清單就代表 LLM 那邊只能老實說「沒查到」）。
    3. /chat 端點正確組裝、正確呼叫 generate_reply。

這支腳本刻意「不」真的呼叫 Claude API（沒有 ANTHROPIC_API_KEY 也能跑），
用假的 generate_reply 抽換掉，只驗證「餵給 LLM 的資料對不對」——這一段錯了，
LLM 再厲害也沒用。真正呼叫 LLM 的部分，請在你自己的機器上設好
ANTHROPIC_API_KEY 之後手動測 /chat，看回覆合不合理。
"""

import json
import os
import sys
from unittest.mock import patch

# 跟 validate_phase1.py 一樣：不管 .env 裡設了什麼，這支腳本都要跑在沒有部署
# 防護的乾淨狀態（/chat 雖然不用後台密碼，但頻率限制打開的話一樣會擋到測試）。
os.environ["ADMIN_USERNAME"] = ""
os.environ["ADMIN_PASSWORD"] = ""
os.environ["GECKO_SHOP_ENABLE_RATE_LIMIT"] = ""

from fastapi.testclient import TestClient

import catalog_search
from database import SessionLocal
from main import app
from models import Product

client = TestClient(app)


def test_find_candidates_by_image_only_confident_matches():
    db = SessionLocal()
    try:
        photo_path = None
        product = db.query(Product).filter(Product.morph_name.like("%橘化%")).first()
        assert product is not None, "測試資料庫裡應該要有橘化商品，先跑過 seed_from_uploads.py"
        photo_path = f"static/uploads/{product.photos[0].file_path}"

        candidates = catalog_search.find_candidates_by_image(db, photo_path)
        assert len(candidates) >= 1, "拿商品自己的照片去查，至少要能查到自己"
        assert candidates[0]["product_id"] == product.id, "相似度最高的應該是照片本人所屬的商品"
        for c in candidates:
            assert c["similarity_percent"] >= catalog_search.MIN_IMAGE_SIMILARITY, "不該有低於信心門檻的結果混進來"
        print(f"[OK] find_candidates_by_image：查到 {len(candidates)} 筆，最像的是 product_id={candidates[0]['product_id']}")
    finally:
        db.close()


def test_find_candidates_by_text_matches_keyword():
    db = SessionLocal()
    try:
        candidates = catalog_search.find_candidates_by_text(db, "橘化")
        assert len(candidates) >= 1, "打「橘化」應該要查得到橘化商品"
        assert all("橘" in c["morph_name"] or "橘" in (c["description"] or "") for c in candidates)
        print(f"[OK] find_candidates_by_text('橘化')：查到 {len(candidates)} 筆，都跟橘化有關")
    finally:
        db.close()


def test_find_candidates_by_text_no_match_returns_empty():
    db = SessionLocal()
    try:
        candidates = catalog_search.find_candidates_by_text(db, "睫角守宮")  # 資料庫裡目前只有豹紋守宮
        assert candidates == [], "查不到的東西就該回傳空清單，不能硬湊"
        print("[OK] find_candidates_by_text('睫角守宮')：查不到，正確回傳空清單（LLM 那邊就會老實說沒貨）")
    finally:
        db.close()


def test_chat_endpoint_text_only_wires_candidates_into_llm():
    captured = {}

    def fake_generate_reply(customer_message, candidates, is_fallback=False, history=None):
        captured["message"] = customer_message
        captured["candidates"] = candidates
        captured["is_fallback"] = is_fallback
        captured["history"] = history
        return "（測試用假回覆，沒有真的呼叫 Claude）"

    prior_turns = [{"role": "user", "text": "哈囉"}, {"role": "assistant", "text": "哈囉，想找什麼樣式呢？"}]

    with patch("main.generate_reply", side_effect=fake_generate_reply):
        resp = client.post(
            "/chat",
            data={"message": "有沒有橘化的", "history": json.dumps(prior_turns, ensure_ascii=False)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "（測試用假回覆，沒有真的呼叫 Claude）"
        assert len(body["candidates"]) >= 1
        assert body["is_fallback"] is False, "關鍵字有比對到東西，就不該是 fallback 模式"
        # body["candidates"] 比餵給 LLM 的多一個 photo_urls 欄位（前端顯示用，LLM 不需要看網址）；
        # 除了這個欄位以外，其他資料要完全一致，才能確保回傳給前端的跟實際依據的資料同一份。
        assert len(captured["candidates"]) == len(body["candidates"])
        for sent, returned in zip(captured["candidates"], body["candidates"]):
            without_photos = {k: v for k, v in returned.items() if k != "photo_urls"}
            assert sent == without_photos, "端點回傳的 candidates（扣掉 photo_urls）要跟真的餵給 LLM 的一致，方便之後記錄稽核"
            assert "photo_urls" in returned
        assert captured["history"] == prior_turns, "前端送來的對話歷史要原封不動轉交給 generate_reply，多輪對話才接得起來"
        for c in body["candidates"]:
            assert "橘" in c["morph_name"] or "橘" in (c["description"] or "")
        print(f"[OK] POST /chat（純文字，含對話歷史）：candidates + history 正確帶入 generate_reply，共 {len(body['candidates'])} 筆")


def test_chat_endpoint_no_match_falls_back_to_highlights():
    captured = {}

    def fake_generate_reply(customer_message, candidates, is_fallback=False, history=None):
        captured["candidates"] = candidates
        captured["is_fallback"] = is_fallback
        return "（測試用假回覆）"

    with patch("main.generate_reply", side_effect=fake_generate_reply):
        resp = client.post("/chat", data={"message": "有沒有睫角守宮"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_fallback"] is True, "關鍵字查不到，應該要改成精選/新進 fallback，而不是空清單"
        assert captured["is_fallback"] is True
        assert len(body["candidates"]) >= 1, "店裡現在有上架商品，fallback 應該要推薦得出東西"
        for c in body["candidates"]:
            assert c["stock_qty"] > 0, "fallback 推薦不該包含庫存 0 的商品"
            assert c["highlight_reason"] in ("featured", "new_arrival")
        print(f"[OK] POST /chat（查不到的東西）：改成 fallback 推薦 {len(body['candidates'])} 筆精選/新進商品，不會瞎掰成「剛好符合」")


def test_find_highlight_candidates_excludes_out_of_stock():
    db = SessionLocal()
    try:
        candidates = catalog_search.find_highlight_candidates(db)
        assert all(c["stock_qty"] > 0 for c in candidates), "fallback 名單不該推薦庫存 0 的商品"
        print(f"[OK] find_highlight_candidates：{len(candidates)} 筆，全部都還有庫存")
    finally:
        db.close()


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # 避免 Windows 主控台編碼印不出中文/符號時整個崩潰

    test_find_candidates_by_image_only_confident_matches()
    test_find_candidates_by_text_matches_keyword()
    test_find_candidates_by_text_no_match_returns_empty()
    test_chat_endpoint_text_only_wires_candidates_into_llm()
    test_chat_endpoint_no_match_falls_back_to_highlights()
    test_find_highlight_candidates_excludes_out_of_stock()
    print("\n全部驗證通過！（LLM 實際回覆品質要自己設定 ANTHROPIC_API_KEY 之後手動測 /chat）")
