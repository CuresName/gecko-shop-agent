"""
客人的問題 -> 飼養知識庫裡最相關的幾個片段。跟 catalog_search.py 的角色一樣：
只負責「查出真的存在的知識片段」，LLM 只負責怎麼講——但飼養知識跟商品價格/
庫存不同，查不到或相關度不夠時不會硬塞給 LLM，讓 llm_reply.py 自然退回它
自己的飼養知識（輔助模式，見 generate_reply 的規則），不是強迫「只能講庫裡
查到的東西」。
"""

import knowledge_store
import text_embeddings

# 相關度低於這個門檻就不附上——避免客人問完全無關的話題時，硬塞幾段不相關的
# 飼養知識進 prompt，反而讓 LLM 誤以為這些資料跟問題有關。
MIN_KNOWLEDGE_SIMILARITY = 35.0


def find_relevant_knowledge(query: str, top_k: int = 3) -> list[dict]:
    """回傳 [{text, species, topic, source, similarity_percent}, ...]，依相關度排序。"""
    vector = text_embeddings.encode_text(query)
    matches = knowledge_store.query_similar(vector, top_k=top_k)

    results = []
    for m in matches:
        similarity_percent = max(0.0, 1 - m["distance"] / 2) * 100
        if similarity_percent < MIN_KNOWLEDGE_SIMILARITY:
            continue
        results.append(
            {
                "text": m["text"],
                "species": m["species"],
                "topic": m["topic"],
                "source": m["source"],
                "similarity_percent": round(similarity_percent, 1),
            }
        )
    return results
