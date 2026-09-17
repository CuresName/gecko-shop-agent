# 守宮商品目錄 + 後台表單（Phase 1）

這是整個「守宮客服 agent」專案的第 0 層：一個商品資料庫，加一個後台表單讓你新增/管理商品。
之後的影像辨識、LLM 回覆，全部都要查這裡的資料，不能瞎猜——所以先把這一層做穩。

## 檔案說明

| 檔案 | 用途 |
|---|---|
| `database.py` | SQLite 連線設定 |
| `models.py` | 資料表結構：`Product`（商品）、`Photo`（照片，先卡好 `is_embedded` 欄位給第 2 階段用）|
| `schemas.py` | API 的資料格式驗證 |
| `main.py` | FastAPI 後端：新增/列表/查詢/更新商品、上傳照片 |
| `static/admin.html` | 後台表單頁面（新增商品、改庫存價格、上下架） |
| `validate_phase1.py` | 端對端驗證腳本，已經全部跑過 ✅ |

## 安裝與啟動

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

打開 http://127.0.0.1:8000/admin.html，就是後台表單畫面：
- 上面的表單新增商品（物種、樣式、價格、庫存、描述、可多選照片）
- 下面的表格列出所有商品，價格/庫存欄位可以直接改完按「儲存」，也可以「上架/下架」

## API 一覽

| Method | Path | 說明 |
|---|---|---|
| POST | `/products` | 新增商品（multipart form，可附照片） |
| GET | `/products` | 列出上架中的商品（`?include_inactive=true` 連下架也列出） |
| GET | `/products/{id}` | 查單一商品完整資訊 |
| PATCH | `/products/{id}` | 更新任意欄位（改價格/庫存/描述/上下架用這個） |
| POST | `/products/{id}/photos` | 補上傳照片 |

## 資料存在哪裡

- 商品資料：`gecko_shop.db`（SQLite 檔案，第一次啟動自動建立）
- 照片檔案：`static/uploads/`

## Phase 2：影像特徵 + 向量搜尋

新增檔案：
| 檔案 | 用途 |
|---|---|
| `embeddings.py` | 把一張照片轉成特徵向量 |
| `vector_store.py` | Chroma 向量資料庫包裝（存向量、找最像的前 k 筆） |
| `backfill_embeddings.py` | 把還沒算過特徵的照片補算一遍（新增商品時已經會自動算，這支是補救用） |
| `validate_phase2.py` | 驗證腳本 |

新增 API：`POST /search/image`（上傳一張照片，回傳最像的前幾個商品 + 相似度）。

### ⚠️ 重要限制：這個環境沒辦法下載真正的 CLIP 模型

原本設計是用開源 CLIP（語意層級的影像理解），但這個雲端沙盒的網路白名單不包含
huggingface.co，沒辦法下載預訓練權重（跟之前抓 Wikimedia 圖片是同一種限制）。所以
`embeddings.py` 現在是用「顏色直方圖 + 邊緣紋理」這種不需要下載任何東西的傳統影像特徵頂著。

**驗證結果老實說**：自己比對自己 10/10 全對（這只是基本檢查，不代表準）；但拿一張橘化
的照片去查，排名前幾名混進了一張雪花系——實測發現目前這個做法容易被「拍攝背景/攝影棚
燈光」蓋過守宮本身的顏色差異（例如同樣黑色背景的照片會被判成很像，不管守宮顏色差多少）。
這不是隨便講講，是 `validate_phase2.py` 實際跑出來的結果。

**這代表現在這個 Phase 2 只證明「管線接得通」（上傳→算特徵→查向量庫→回傳候選商品），
還不能保證比對準確，先不要拿去給真的客人用。**

兩個改善方向：
1. **治本**：把這個專案部署到有完整網路的地方（自己電腦、Railway...）之後，把
   `embeddings.py` 的 `encode_image()` 換成真的呼叫 CLIP 模型——`vector_store.py`、
   `main.py` 的 API 完全不用改，這就是當初把影像特徵抽成獨立檔案的用意。
2. **治標（現在就能做）**：以後拍商品照片時盡量固定同一個背景/同一個容器/同樣的光線，
   這樣顏色直方圖才反映得出守宮本身的差異，而不是每次攝影棚設定不同造成的雜訊。

### 使用方式

```bash
python backfill_embeddings.py   # 把還沒算過特徵的照片補算一遍
python validate_phase2.py       # 看目前的比對品質
```

之後透過 `/products` 或 `/products/{id}/photos` 上傳的新照片，系統會自動算好特徵，
不需要手動再跑 `backfill_embeddings.py`（除非中途算特徵失敗，那種情況才需要補跑）。

## Phase 3：LLM 自動回覆（有防呆機制）

新增檔案：
| 檔案 | 用途 |
|---|---|
| `catalog_search.py` | 把客人的問題（照片或文字）轉成「資料庫裡真的存在」的候選商品清單 |
| `llm_reply.py` | 把候選商品清單交給 Claude，組成自然的客服回覆 |
| `validate_phase3.py` | 驗證腳本（不需要 API key 也能跑，見下方說明） |

新增 API：`POST /chat`（form-data：`message` 文字、`photo` 照片，兩個都可選，
至少要有一個），回傳 `{"reply": "...", "candidates": [...]}`。`candidates` 就是
這次回覆實際依據的商品資料，之後做記錄/人工覆核（Phase 5）會用到。

### 核心防呆邏輯：LLM 不能自己編價格/庫存

不管客人怎麼問，流程都是「先查資料庫，再讓 LLM 組句子」，順序不能反過來：

1. 客人有附照片 → `catalog_search.find_candidates_by_image`：用 Phase 2 的向量搜尋，
   而且只留下相似度 ≥ 55% 的結果（門檻可以調 `catalog_search.MIN_IMAGE_SIMILARITY`）。
   信心不夠的直接濾掉，不會硬塞一個不像的商品給 LLM。
2. 客人只有打字 → `catalog_search.find_candidates_by_text`：關鍵字比對
   `species`/`morph_name`/`description` 欄位（還沒有文字版語意搜尋，先用 LIKE 頂著，
   細節見程式碼註解）。
3. 把查到的候選清單（可能是空的）交給 `llm_reply.generate_reply`，system prompt
   裡明文規定：只能講清單裡有的東西，清單是空的就老實說沒查到，不能瞎掰、不能
   在庫存是 0 的時候說可以賣。

`main.py` 的 `/chat` 端點會把 `candidates` 原封不動回傳出來，方便你檢查「這次
回覆到底是根據哪些資料講的」——這是可以稽核的，不是黑盒子。

### 已知限制（老實講）

- **文字搜尋還是關鍵字比對，不是真的語意理解**：例如客人問「有沒有橘色的」，
  抓得到「橘」這個字才查得到；如果客人的用詞跟資料庫欄位差太多（例如用「柑橘色」
  而資料庫寫「橘化」），目前抓不到。之後換成 `embeddings_clip.py` 裡的
  `encode_text()`（CLIP 同時懂文字和圖片）可以大幅改善，屆時只要換掉
  `catalog_search.py` 裡的比對邏輯，`main.py` 不用動。
- **物種欄位（species）刻意不用子字串模糊比對**：因為店裡目前只賣「豹紋守宮」，
  "守宮" 這兩個字幾乎每筆商品都中，模糊比對反而會把問其他物種的客人也錯配過來。
  這代表目前對「泛問物種」（例如「你們都賣什麼？」）不會有專門的回覆邏輯，
  抓不到關鍵字就會回傳空清單——這種情況下 LLM 會老實說沒查到，而不是亂猜，
  但客人體驗上可能會覺得「怎麼問都查不到」，這是之後可以再補的地方（例如
  偵測到這類泛問就直接回傳全部上架商品的清單）。
- **這支專案本身還是用 Phase 2 的直方圖版 `embeddings.py`**，圖片比對準確度的
  限制（見上面 Phase 2 段落）也會直接反映在 `/chat` 的照片查詢結果上。

### 使用方式

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # Windows 用 set ANTHROPIC_API_KEY=sk-ant-...
python -m pip install -r requirements.txt
python validate_phase3.py             # 不需要 API key，驗證查詢/防呆邏輯對不對
uvicorn main:app --reload             # 需要 API key，真的呼叫 Claude 才會用到
```

沒有 `ANTHROPIC_API_KEY` 的話，`validate_phase3.py` 一樣能跑完（它把
`generate_reply` 抽換成假函式，只驗證「餵給 LLM 的資料對不對」），但實際打
`POST /chat` 會因為 `anthropic` SDK 找不到 API key 而失敗——去
https://console.anthropic.com 申請一組 key，設定成環境變數即可。
