"""
把 knowledge/*.md 的飼養指南切成 chunk、算好向量、存進 knowledge_store.py 的
Chroma collection。每次改完 knowledge/ 底下的內容，重跑這支就會更新索引
（用檔名當 source，重新 ingest 同一個檔案前會先清掉那個檔案舊的 chunk，
不會留下改標題/刪段落後的殭屍資料）。

切法：依 `##` 標題切（一個主題一個 chunk），這樣切是因為每個主題本來就是
一個自然的語意單位，比死板的「每 300-500 字硬切」更合理，也不會把同一個主題
的內容攔腰切斷、破壞語意完整性。只有單一主題內容真的很長時，才會再往下細切。

用法：
    python ingest_knowledge.py
"""

import glob
import os
import re

import knowledge_store
import text_embeddings

KNOWLEDGE_DIR = "knowledge"
MAX_CHUNK_CHARS = 600  # 單一主題內容超過這個長度才會再往下細切


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析開頭的 ```---\nspecies: 豹紋守宮\n---``` 區塊，回傳 (metadata, 剩下的內容)。"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not match:
        return {}, content
    front, rest = match.groups()
    meta = {}
    for line in front.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, rest


def strip_comments_and_check_empty(text: str) -> str:
    """去掉 `<!-- 範本提示 -->` 這種註解，回傳實際內容；如果去掉註解後是空的，
    代表這個主題還沒填，回傳空字串（呼叫端會跳過，不會把範本提示當成真內容索引進去）。"""
    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    return cleaned


def split_into_topics(body: str) -> list[tuple[str, str]]:
    """依 `## 標題` 切成 [(標題, 內容), ...]。"""
    sections = re.split(r"\n##\s+", body)
    topics = []
    for section in sections[1:]:  # 第一段是 `# 品種飼養指南` 大標題跟前言，不是主題內容
        lines = section.split("\n", 1)
        title = lines[0].strip()
        text = lines[1] if len(lines) > 1 else ""
        topics.append((title, text))
    return topics


def sub_split(text: str, max_chars: int) -> list[str]:
    """內容太長才會用到：依段落（空行）切，再視需要合併鄰近小段落湊到接近上限。"""
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) > max_chars:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks or [text]


def ingest_file(path: str) -> int:
    filename = os.path.basename(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()

    meta, body = parse_frontmatter(content)
    species = meta.get("species", "")
    if not species:
        print(f"  [跳過] {filename} 沒有 frontmatter 裡的 species 欄位，不知道要歸到哪個品種")
        return 0

    knowledge_store.delete_by_source(filename)

    count = 0
    for topic, raw_text in split_into_topics(body):
        cleaned = strip_comments_and_check_empty(raw_text)
        if not cleaned:
            continue  # 範本提示還沒換成真內容，跳過，不索引空 chunk

        pieces = sub_split(cleaned, MAX_CHUNK_CHARS)
        for i, piece in enumerate(pieces):
            chunk_id = f"{filename}::{topic}::{i}"
            vector = text_embeddings.encode_text(piece)
            knowledge_store.upsert_chunk(
                chunk_id=chunk_id,
                vector=vector,
                text=piece,
                metadata={"species": species, "topic": topic, "source": filename},
            )
            count += 1
    return count


def main() -> None:
    paths = sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md")))
    paths = [p for p in paths if os.path.basename(p) != "README.md"]

    if not paths:
        print(f"{KNOWLEDGE_DIR}/ 底下沒有找到 .md 檔案")
        return

    total = 0
    for path in paths:
        n = ingest_file(path)
        print(f"{os.path.basename(path)}：索引了 {n} 個 chunk")
        total += n

    print(f"\n完成！總共 {total} 個 chunk，knowledge_store 裡現在共 {knowledge_store.count()} 筆。")
    if total == 0:
        print("（目前都還是範本提示、沒有真的內容，寫完 knowledge/*.md 裡的段落後重跑這支就會看到 chunk 數量增加）")


if __name__ == "__main__":
    main()
