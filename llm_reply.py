"""
LLM 回覆生成
============
只做一件事：把「客人的訊息」+「資料庫實際查到的候選商品清單」(來自
catalog_search.py) 丟給 Claude，換回一段可以直接回給客人的文字。

核心規則寫在 system prompt 裡：不管客人怎麼問，LLM 都只能講候選清單裡面
明確列出的商品/價格/庫存，絕對不能自己編。這一點比「聽起來多聰明」重要
太多——講錯庫存或價格，對一個真的在賣東西的商家是實際的金錢/信任風險。

需要環境變數 ANTHROPIC_API_KEY 才能真的呼叫 Claude（去
https://console.anthropic.com 申請）。本機測試時可以用 export/set 設定，
之後部署時記得也要在伺服器上設定這個環境變數，不要把 key 寫死在程式碼裡。
"""

import json
import os

import anthropic

MODEL = os.environ.get("GECKO_SHOP_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = """你是一間守宮（壁虎）專賣店的客服助理，個性親切自然，一律用繁體中文回覆客人，
語氣像真人小編在聊天，不要條列式、不要出現「候選商品」「清單」這種內部用詞。

嚴格規則（一定要遵守，不可以違反）：
1. 你只能提到「目前查到的商品資訊」裡明確列出的商品、價格、庫存數量。
2. 清單裡沒有的商品、價格、庫存，絕對不能自己編造或用猜測的方式回答，
   就算客人一直追問也一樣。
3. price、stock_qty 這兩個數字，回覆時必須跟 JSON 裡寫的完全一模一樣，
   一個數字都不能改、不能四捨五入、不能憑印象重講一次——你不是在「回想」這個數字，
   是要直接照抄。寫出價格前，先在心裡對照一次 JSON 裡那個商品的 price 欄位。
4. 如果「目前查到的商品資訊」是空的，或客人問的東西不在裡面，就老實說
   目前沒有查到符合的商品，可以請客人提供更多細節（例如照片、想要的
   顏色/花紋），不要假裝有貨、不要隨口報價。
5. 如果某個商品的庫存數量是 0，要告訴客人目前缺貨，不能說可以賣。
6. 回覆盡量簡短、自然，像在跟朋友聊天，一次不要講太多商品。
"""


def generate_reply(
    customer_message: str,
    candidates: list[dict],
    is_fallback: bool = False,
    history: list[dict] | None = None,
) -> str:
    """candidates 來自 catalog_search.find_candidates_by_image/find_candidates_by_text，
    每筆至少有 product_id/species/morph_name/price/stock_qty/description。

    is_fallback=True 代表這份清單來自 catalog_search.find_highlight_candidates——
    客人問的話題太籠統，或關鍵字剛好比對不到任何商品，所以改成「店裡目前的精選/新進」，
    不是「精準符合客人問題」的比對結果，要讓 LLM 用對應的語氣回覆（不能講得像是剛好找到客人要的）。

    history：這通對話之前的來回紀錄（客人在同一個瀏覽器分頁裡問過的），格式是
    [{"role": "user"/"assistant", "text": "..."}, ...]，讓 LLM 聽得懂「這隻」「剛剛那個」
    這種需要接續上下文才懂的問法。只有「這一輪」的候選商品才會附上 candidates 佐證資料——
    之前幾輪 LLM 自己講過的話，原封不動當作歷史紀錄餵回去就好，不用重新附證據。
    """
    client = anthropic.Anthropic()  # 會自動讀取環境變數 ANTHROPIC_API_KEY

    if not candidates:
        context_block = "（目前沒有查到符合的商品）"
    else:
        context_block = json.dumps(candidates, ensure_ascii=False, indent=2)
        if is_fallback:
            context_block += (
                "\n\n（提醒：這份清單不是精準比對客人問題查到的，是店裡目前的精選/新進商品——"
                "客人的問題可能太籠統，或剛好沒有比對到符合的商品，所以先用這份清單介紹店裡目前有什麼，"
                "回覆時要讓客人知道這是「目前店裡有的商品」，不能講得像是剛好符合他要的東西。）"
            )

    user_content = f"""客人的訊息：
{customer_message or "（客人只傳了照片，沒有打字）"}

目前查到的商品資訊（只能根據這裡面的內容回答，不可以提到這份資訊以外的商品/價格/庫存）：
{context_block}
"""

    messages = []
    for turn in history or []:
        role = turn.get("role")
        text = turn.get("text")
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_content})

    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=messages,
        # 這個 SDK 版本沒有 temperature 這種傳統取樣參數了，改用 effort 控制
        # 「多花點心思再回答」——這裡是在陳述價格/庫存這種不能出錯的事實，
        # 不是自由發揮的創意寫作，效果值得拉高。
        output_config={"effort": "high"},
    )
    return response.content[0].text
