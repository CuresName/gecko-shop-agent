"""
後台用：上傳照片時，讓 Claude 看照片幫忙猜物種/樣式，省去手動輸入。
=====================================================================
這是給「你自己（店家）新增商品」用的輔助工具，跟 llm_reply.py（回覆客人）是
兩件不同的事——這裡只負責看圖片猜標籤，回傳建議值讓你在表單裡自己確認/修改，
不會自動存進資料庫，也不會影響客人看到的回覆邏輯。

跟原本專案「10 筆商品的樣式是 AI 依外觀猜的，不是基因驗證」的做法一致——
這支只是把「Claude 幫忙猜」這個步驟做成後台按鈕，取代手動一張一張看照片猜。
"""

import base64
import json
import os

import anthropic

MODEL = os.environ.get("GECKO_SHOP_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = """你是爬蟲寵物店的商品標籤助理，專長是看豹紋守宮（leopard gecko）的照片，
依外觀猜測物種與花紋樣式（morph），給店家新增商品時參考用。

規則：
1. 只能根據照片裡看得到的顏色、花紋、體型特徵判斷，不能瞎猜品種以外的資訊（不要編價格、
   不要編性別/月齡，除非照片裡有明確標示）。
2. species（物種）欄位只填中文物種俗名（例如「豹紋守宮」），不要加英文或括號——
   這個欄位要跟資料庫裡其他商品的格式一致，是後端用來比對物種的關鍵欄位。
3. morph_name（樣式）盡量用常見的守宮飼育術語（例如：橘化/Tangerine、黃化/High Yellow、
   雪花系/Snow、條紋系/Striped、叢林系/Jungle、瑪紋/Mack Snow 等），格式是
   「中文名稱 (English Name)」，不確定就給一個最接近的合理猜測，不要留空。
4. description 欄位用一兩句話描述外觀特徵（顏色、花紋、體態），不要提到價格/庫存/性別
   除非照片裡明確可見。
5. 只能輸出這個 JSON 格式，不要有其他文字：
   {"species": "...", "morph_name": "...", "description": "...", "confidence_note": "..."}
   confidence_note 是一句話老實講這個判斷有多不確定（例如「花紋不算典型，建議再核對」）。
"""


def suggest_tags(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """回傳 {"species", "morph_name", "description", "confidence_note"}，
    都是「建議值」——後台表單會把這些值先填進欄位，店家送出前還是要自己看過確認，
    跟人工新增商品時「先猜再覆核」的流程一致，不是最終定案。
    """
    client = anthropic.Anthropic()

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": image_b64},
                    },
                    {"type": "text", "text": "請幫我猜這隻守宮的物種跟樣式，用規定的 JSON 格式回答。"},
                ],
            }
        ],
    )

    raw_text = response.content[0].text.strip()
    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # 萬一 LLM 沒有乖乖只回 JSON（例如前後多包了說明文字），退而求其次找出中間的 JSON 片段
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1:
            result = json.loads(raw_text[start : end + 1])
        else:
            raise ValueError(f"AI 回覆不是預期的 JSON 格式：{raw_text}")

    return {
        "species": result.get("species", ""),
        "morph_name": result.get("morph_name", ""),
        "description": result.get("description", ""),
        "confidence_note": result.get("confidence_note", ""),
    }
