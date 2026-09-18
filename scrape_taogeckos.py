"""
一次性腳本：從 taogeckos.com（已授權使用其型錄資料）抓守宮品項 + 周邊用品，
存成一份 manifest JSON + 把照片下載到本機資料夾，給 import_taogeckos.py 接手匯入。

刻意分成「抓資料」跟「寫進資料庫」兩支獨立腳本——抓資料這步可以重複執行、
先人工看過 manifest.json 再決定要不要匯入，不會一抓到就直接變成正式商品。

範圍（跟店家確認過的做法）：
- 守宮品項：只抓真的是「守宮」的分類（豹紋/睫角/瘤尾/肥尾/巨人），不含藍舌蜥、
  鬆獅蜥、澤龜/陸龜、球蟒、豬鼻蛇、王蛇這些非守宮的其他爬蟲——跟這個客服機器人
  「守宮專賣店」的人設一致。每個分類抓完所有頁面，只留「有標價」的（沒標價的
  型錄項目代表已經賣掉/保留，不當作現貨匯入）。
- 周邊用品：涵蓋全部 9 個子分類，但每個子分類只抓第一頁、而且同名商品只留一筆
  代表性項目（例如「香檳木」每一筆都是不同的一塊實木、單價不同，全部匯入沒有
  意義，客人也不會在意是哪一塊）——避免匯入幾百筆幾乎一樣的品項。

用法：
    python scrape_taogeckos.py
輸出：
    scrape_staging/manifest.json
    scrape_staging/images/<source_id>_<n>.jpg
"""

import json
import os
import re
import time

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings()

STAGING_DIR = "scrape_staging"
IMAGES_DIR = os.path.join(STAGING_DIR, "images")

GECKO_CATEGORIES = {
    16817: "睫角守宮",
    16818: "瘤尾守宮",
    16819: "肥尾守宮",
    16820: "豹紋守宮",
    22299: "巨人守宮",
}

ACCESSORY_CATEGORIES = {
    16791: "飼養盒、缸",
    16794: "躲避屋、造景",
    16795: "食水盆",
    16796: "飼料",
    16797: "墊材",
    16798: "營養品",
    16799: "加溫墊/燈具",
    16800: "其他",
    23568: "香檳木",
}

MAX_ACCESSORY_ITEMS_PER_CATEGORY = 6

HEADERS = {"User-Agent": "Mozilla/5.0 (gecko-shop-catalog-import; authorized data import)"}


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=20, verify=False)
    r.encoding = r.apparent_encoding
    r.raise_for_status()
    return r.text


def parse_listing(html: str) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for li in soup.select("ul.products-list li.item"):
        a = li.find("a")
        if not a:
            continue
        name = (li.select_one(".name") or a).get_text(strip=True)
        price_text = (li.select_one(".price b") or {}).get_text(strip=True) if li.select_one(".price b") else ""
        price_match = re.search(r"[\d,]+", price_text or "")
        price = float(price_match.group().replace(",", "")) if price_match else None

        img = li.select_one("figure.pic img")
        img_url = img["src"] if img and img.has_attr("src") else None

        href = a.get("href", "")
        id_match = re.search(r"id=(\d+)", href)
        source_id = id_match.group(1) if id_match else None

        if not source_id or not img_url:
            continue

        items.append({"source_id": source_id, "name": name, "price": price, "thumb_url": img_url})

    max_page = 1
    for a in soup.select("ul.pageList a, .page a"):
        href = a.get("href", "")
        m = re.search(r"page=(\d+)", href)
        if m:
            max_page = max(max_page, int(m.group(1)))

    return items, max_page


def fetch_detail_images(source_id: str) -> list[str]:
    url = f"https://www.taogeckos.com/products/info.php?id={source_id}&title_id="
    try:
        html = fetch(url)
    except requests.RequestException as e:
        print(f"  [警告] 抓詳細頁失敗 id={source_id}: {e}")
        return []
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for img in soup.select("#bx-pager img"):
        src = img.get("src")
        if src:
            urls.append(src)
    return urls


def download_image(url: str, dest_path: str) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return True
    except requests.RequestException as e:
        print(f"  [警告] 下載圖片失敗 {url}: {e}")
        return False


def scrape_gecko_category(second_id: int, species: str) -> list[dict]:
    print(f"[守宮] {species} (second_id={second_id})")
    results = []
    page = 1
    while True:
        url = (
            f"https://www.taogeckos.com/products/index.php?"
            f"page={page}&useno=tao&group_id=14557&second_id={second_id}"
        )
        html = fetch(url)
        items, max_page = parse_listing(html)
        priced = [i for i in items if i["price"] is not None]
        print(f"  page {page}/{max_page}: {len(items)} 筆，{len(priced)} 筆有標價（現貨）")
        for item in priced:
            detail_images = fetch_detail_images(item["source_id"])
            image_urls = detail_images or [item["thumb_url"]]
            results.append(
                {
                    "product_type": "gecko",
                    "species": species,
                    "raw_name": item["name"],
                    "price": item["price"],
                    "source_id": item["source_id"],
                    "image_urls": image_urls[:3],  # 最多留 3 張，不用整組相簿都拉
                    "source_url": f"https://www.taogeckos.com/products/info.php?id={item['source_id']}",
                }
            )
            time.sleep(0.3)
        if page >= max_page:
            break
        page += 1
    return results


def scrape_accessory_category(second_id: int, category_label: str) -> list[dict]:
    print(f"[周邊] {category_label} (second_id={second_id})")
    url = (
        f"https://www.taogeckos.com/products/index.php?"
        f"page=1&useno=tao&group_id=14558&second_id={second_id}"
    )
    html = fetch(url)
    items, _ = parse_listing(html)
    priced = [i for i in items if i["price"] is not None]

    seen_names = set()
    picked = []
    for item in priced:
        if item["name"] in seen_names:
            continue
        seen_names.add(item["name"])
        picked.append(item)
        if len(picked) >= MAX_ACCESSORY_ITEMS_PER_CATEGORY:
            break

    print(f"  {len(items)} 筆，{len(priced)} 筆有標價，取代表性 {len(picked)} 筆")

    results = []
    for item in picked:
        results.append(
            {
                "product_type": "accessory",
                "species": category_label,  # 借用 species 欄位放用品分類
                "raw_name": item["name"],
                "price": item["price"],
                "source_id": item["source_id"],
                "image_urls": [item["thumb_url"]],
                "source_url": f"https://www.taogeckos.com/products/info.php?id={item['source_id']}",
            }
        )
        time.sleep(0.2)
    return results


def main() -> None:
    os.makedirs(IMAGES_DIR, exist_ok=True)

    manifest = []
    for second_id, species in GECKO_CATEGORIES.items():
        manifest.extend(scrape_gecko_category(second_id, species))
    for second_id, label in ACCESSORY_CATEGORIES.items():
        manifest.extend(scrape_accessory_category(second_id, label))

    print(f"\n共 {len(manifest)} 筆商品，開始下載照片...")
    for entry in manifest:
        local_paths = []
        for i, img_url in enumerate(entry["image_urls"]):
            ext = os.path.splitext(img_url.split("?")[0])[1] or ".jpg"
            filename = f"{entry['source_id']}_{i}{ext}"
            dest = os.path.join(IMAGES_DIR, filename)
            if not os.path.exists(dest):
                download_image(img_url, dest)
            if os.path.exists(dest):
                local_paths.append(filename)
        entry["local_images"] = local_paths

    manifest_path = os.path.join(STAGING_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"完成！manifest 存在 {manifest_path}，照片存在 {IMAGES_DIR}/")
    print("接下來看一下 manifest.json 內容合不合理，再跑 import_taogeckos.py 匯入。")


if __name__ == "__main__":
    main()
