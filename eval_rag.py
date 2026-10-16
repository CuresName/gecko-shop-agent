"""
RAG 飼養知識庫的評測集：15-20 題「客人實際會問的問題」，涵蓋三個品種 x 四個
基本主題、沒寫內容的品種/進階主題（測試有沒有乖乖退回 LLM 自己的知識而不是
硬掰）、混合問題（同時問飼養知識+庫存）、完全不相關的問題（測試不會硬塞無關
資料）。

分兩段跑：
1. **檢索準確度**（免費、快）：只測 knowledge_search 抓到的 chunk 對不對，
   不呼叫 Claude。
2. **端對端回覆品質**（會花一點 token，較慢，預設不跑）：加 `--full` 才會跑，
   實際呼叫 generate_reply，把完整回覆存成逐字稿讓你自己看有沒有瞎掰、
   語氣自不自然——這塊沒辦法完全自動判斷對錯，所以用「存逐字稿讓人看」取代
   自動判題，比硬做一個不準的自動評分更誠實。

用法：
    python eval_rag.py          # 只測檢索準確度
    python eval_rag.py --full   # 加測端對端回覆（會呼叫 Claude API，花一點錢）
"""

import sys

import catalog_search
import knowledge_search
from database import SessionLocal

# 每一題：
#   question: 客人的訊息
#   expect: "knowledge" | "uncovered" | "mixed" | "unrelated"
#   species / topic: expect="knowledge" 時，預期抓到的 chunk 應該符合這個品種/主題
TEST_CASES = [
    # ---- 豹紋守宮 x 4 主題 ----
    {"question": "豹紋守宮的濕度要抓多少才對？", "expect": "knowledge", "species": "豹紋守宮", "topic": "溫濕度"},
    {"question": "豹紋守宮大概多久餵一次，餵什麼？", "expect": "knowledge", "species": "豹紋守宮", "topic": "餵食"},
    {"question": "養豹紋守宮的飼養箱要多大、要放什麼？", "expect": "knowledge", "species": "豹紋守宮", "topic": "飼養箱佈置"},
    {"question": "去店裡怎麼看一隻豹紋守宮健不健康？", "expect": "knowledge", "species": "豹紋守宮", "topic": "挑選健康個體"},
    # ---- 睫角守宮 x 4 主題 ----
    {"question": "睫角守宮適合的濕度大概多高？", "expect": "knowledge", "species": "睫角守宮", "topic": "溫濕度"},
    {"question": "睫角守宮吃什麼、多久餵一次？", "expect": "knowledge", "species": "睫角守宮", "topic": "餵食"},
    {"question": "睫角守宮的缸要怎麼佈置，需要樹枝嗎？", "expect": "knowledge", "species": "睫角守宮", "topic": "飼養箱佈置"},
    {"question": "第一次買睫角守宮要注意挑哪種狀態的？", "expect": "knowledge", "species": "睫角守宮", "topic": "挑選健康個體"},
    # ---- 肥尾守宮 x 4 主題 ----
    {"question": "肥尾守宮的溫度要控制在幾度？", "expect": "knowledge", "species": "肥尾守宮", "topic": "溫濕度"},
    {"question": "肥尾守宮餵食習慣跟豹紋守宮一樣嗎？", "expect": "knowledge", "species": "肥尾守宮", "topic": "餵食"},
    {"question": "肥尾守宮的飼養箱需要準備什麼？", "expect": "knowledge", "species": "肥尾守宮", "topic": "飼養箱佈置"},
    {"question": "怎麼判斷肥尾守宮是不是健康的？", "expect": "knowledge", "species": "肥尾守宮", "topic": "挑選健康個體"},
    # ---- 沒寫內容的品種/進階主題：測試有沒有乖乖退回 LLM 自己的知識 ----
    {"question": "瘤尾守宮濕度要怎麼抓？", "expect": "uncovered"},
    {"question": "豹紋守宮的品系/基因要怎麼分辨？", "expect": "uncovered"},
    {"question": "巨人守宮會不會很難養？", "expect": "uncovered"},
    # ---- 混合：飼養知識 + 庫存都問 ----
    {"question": "豹紋守宮要怎麼養？你們現在有賣嗎，多少錢？", "expect": "mixed", "species": "豹紋守宮", "topic": "溫濕度"},
    {"question": "睫角守宮好養嗎，店裡有現貨的嗎？", "expect": "mixed", "species": "睫角守宮"},
    # ---- 完全不相關：測試不會硬塞無關資料 ----
    {"question": "你們禮拜幾公休？", "expect": "unrelated"},
    {"question": "可以用現金付款嗎？", "expect": "unrelated"},
]


def run_retrieval_eval() -> list[dict]:
    results = []
    store_empty = knowledge_search.knowledge_store.count() == 0

    for case in TEST_CASES:
        chunks = knowledge_search.find_relevant_knowledge(case["question"], top_k=3)
        top = chunks[0] if chunks else None

        if case["expect"] in ("knowledge", "mixed") and "species" in case:
            if store_empty:
                status = "NO_DATA"
            elif top and top["species"] == case["species"] and (
                "topic" not in case or top["topic"] == case["topic"]
            ):
                status = "PASS"
            else:
                status = "FAIL"
        elif case["expect"] == "uncovered":
            # 沒寫內容的品種/主題：理想狀況是完全查不到（相關度門檻會把弱相關的濾掉），
            # 查到的話不算致命錯誤，但要人工看一下抓到的是不是真的相關
            status = "PASS" if not chunks else "REVIEW"
        elif case["expect"] == "unrelated":
            status = "PASS" if not chunks else "REVIEW"
        else:
            status = "SKIP"

        results.append({**case, "status": status, "top_chunk": top})

    return results


def print_retrieval_summary(results: list[dict]) -> None:
    print(f"{'狀態':<8}{'預期':<12}{'問題'}")
    print("-" * 70)
    for r in results:
        print(f"{r['status']:<8}{r['expect']:<12}{r['question']}")
        if r["top_chunk"]:
            print(f"         -> 抓到：{r['top_chunk']['species']} / {r['top_chunk']['topic']} "
                  f"(相關度 {r['top_chunk']['similarity_percent']}%)")

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("-" * 70)
    print("統計：", counts)

    if counts.get("NO_DATA"):
        print("\n[提醒] 有題目因為 knowledge_store 還是空的沒辦法判斷對錯——"
              "先把 knowledge/*.md 內容寫完、跑 python ingest_knowledge.py 索引之後再跑這支。")
    if counts.get("FAIL"):
        print("\n[警告] 有題目抓到的 chunk 跟預期的品種/主題對不上，檢查一下對應的")
        print("        knowledge/*.md 內容是不是太籠統、或者 MIN_KNOWLEDGE_SIMILARITY 門檻要調整。")


def run_full_eval(results: list[dict]) -> None:
    """端對端呼叫 generate_reply，把完整逐字稿存成檔案讓你自己讀——
    回覆品質/有沒有瞎掰這種事沒辦法完全自動判斷，用人工看逐字稿取代不準的自動判題。
    """
    from llm_reply import generate_reply

    db = SessionLocal()
    transcript_lines = []
    try:
        for r in results:
            question = r["question"]
            candidates = []
            if r["expect"] == "mixed":
                candidates = catalog_search.find_candidates_by_text(db, question)

            chunks = knowledge_search.find_relevant_knowledge(question, top_k=3)
            reply = generate_reply(question, candidates, knowledge_chunks=chunks)

            transcript_lines.append(f"問：{question}")
            transcript_lines.append(f"（預期類型：{r['expect']}，抓到 {len(chunks)} 個知識片段，{len(candidates)} 筆商品候選）")
            transcript_lines.append(f"答：{reply}")
            transcript_lines.append("-" * 70)
    finally:
        db.close()

    out_path = "eval_rag_transcript.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(transcript_lines))
    print(f"\n完整逐字稿存在 {out_path}，自己讀一遍看回覆自不自然、有沒有瞎掰。")


def main() -> None:
    results = run_retrieval_eval()
    print_retrieval_summary(results)

    if "--full" in sys.argv:
        print("\n開始端對端測試（會呼叫 Claude API）...")
        run_full_eval(results)
    else:
        print("\n（只跑了檢索準確度測試，沒呼叫 Claude。加 --full 參數可以測完整回覆品質。）")


if __name__ == "__main__":
    main()
