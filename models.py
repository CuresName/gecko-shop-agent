"""
資料表結構
==========
Product：一隻/一批守宮商品的完整資訊 —— 之後 LLM 回覆客人時，
         只能陳述這張表裡真的存在的資料，不能瞎猜。
Photo：商品照片，is_embedded 先預設 False —— 第 2 階段做影像特徵/向量搜尋時，
       會回頭把還沒算過特徵的照片處理掉，這裡先留好這個欄位卡位。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    species = Column(String(100), nullable=False)       # 物種，例如「豹紋守宮」「冠海守宮」
    morph_name = Column(String(150), nullable=False)    # 樣式/花紋名稱，例如「Tangerine」
    price = Column(Float, nullable=False)
    stock_qty = Column(Integer, nullable=False, default=0)
    description = Column(Text, default="")
    is_active = Column(Boolean, default=True)            # 下架用這個標記，不做硬刪除
    is_featured = Column(Boolean, default=False)          # 精選/熱銷：沒有訂單資料可以自動算，改由後台手動標記
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    photos = relationship("Photo", back_populates="product", cascade="all, delete-orphan")


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    file_path = Column(String(300), nullable=False)      # 相對於 static/uploads 的檔名
    is_embedded = Column(Boolean, default=False)          # 第 2 階段：算過影像特徵沒有
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="photos")


class ChatLog(Base):
    """Phase 5：每一通 /chat 的問答都留一筆紀錄，方便事後檢查系統講了什麼。

    低信心（is_low_confidence）的判定：
      - 客人有附照片，但比對不到任何候選商品，或最像的那筆相似度也不夠高
      - 客人只有打字，關鍵字完全比對不到（觸發了 catalog_search 的 fallback 推薦）
    只有被標記低信心的對話，才會多留一份客人上傳的原始照片（photo_path），
    而且設了保留期限（photo_expires_at）——一般對話不留照片，兼顧稽核用途跟隱私。
    """

    __tablename__ = "chat_logs"

    id = Column(Integer, primary_key=True, index=True)
    customer_message = Column(Text, default="")
    reply = Column(Text, nullable=False)
    candidates_json = Column(Text, default="[]")          # candidates 清單，存成 JSON 字串
    is_fallback = Column(Boolean, default=False)
    is_low_confidence = Column(Boolean, default=False)
    low_confidence_reason = Column(String(200), default="")
    had_photo = Column(Boolean, default=False)             # 這通對話客人是否有附照片（不管有沒有留底）
    photo_path = Column(String(300), nullable=True)        # 相對於 GECKO_SHOP_LOG_PHOTO_DIR，只有低信心才會有值
    photo_expires_at = Column(DateTime, nullable=True)      # 保留期限一到，cleanup 腳本會把檔案+這個欄位一起清掉
    reviewed = Column(Boolean, default=False)               # 後台覆核佇列裡標記「已經看過」用
    created_at = Column(DateTime, default=datetime.utcnow)
