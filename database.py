"""
資料庫連線設定
==============
第 0 層的基礎：一個 SQLite 檔案 (gecko_shop.db)，跟 Funbox 用的方式一樣，
簡單、不需要另外架資料庫伺服器，量大了以後也可以直接換成 Postgres
（SQLAlchemy 的寫法幾乎不用改）。
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 可以用環境變數覆寫（驗證腳本、將來的 Postgres 部署都靠這個切換，本機開發沒設定就用預設的 SQLite）
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./gecko_shop.db")

# Railway（跟大多數雲端 Postgres 服務一樣）給的連線字串開頭通常是 postgresql://，
# 但這個專案裝的是 psycopg3，SQLAlchemy 要看到 postgresql+psycopg:// 才會用它——
# 這裡直接幫忙轉換，不用你自己手動改 Railway 給的字串。
if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.split("://", 1)[1]
    DATABASE_URL = f"postgresql+psycopg://{DATABASE_URL}"

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依賴注入用：每個請求開一個 session，用完自動關閉。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
