"""
Pydantic schemas —— API 進出資料的格式驗證。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PhotoOut(BaseModel):
    id: int
    file_path: str
    is_embedded: bool

    model_config = ConfigDict(from_attributes=True)


class ProductBase(BaseModel):
    species: str
    morph_name: str
    color: str = ""
    pattern: str = ""
    price: float
    stock_qty: int = 0
    description: str = ""


class ProductUpdate(BaseModel):
    """更新用：所有欄位都選填，只送你想改的欄位就好（這是你要的「庫存、價格表單」）。"""
    species: str | None = None
    morph_name: str | None = None
    color: str | None = None
    pattern: str | None = None
    price: float | None = None
    stock_qty: int | None = None
    description: str | None = None
    is_active: bool | None = None
    is_featured: bool | None = None


class ProductOut(ProductBase):
    id: int
    is_active: bool
    is_featured: bool
    created_at: datetime
    updated_at: datetime
    photos: list[PhotoOut] = []

    model_config = ConfigDict(from_attributes=True)
