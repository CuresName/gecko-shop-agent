"""
把還沒算過特徵的照片（Photo.is_embedded == False）都補算一遍，存進 Chroma。
新增商品、補傳照片之後，都可以重跑這支腳本（已經算過的不會重算）。

之後想排程自動跑也可以，或是接到 main.py 的新增照片流程裡自動觸發——
先獨立成一支腳本方便你自己手動跑、方便除錯。
"""

import os
import tempfile

from sqlalchemy.orm import Session

import storage
import vector_store
from database import Base, SessionLocal, engine
from embeddings import encode_image
from models import Photo

Base.metadata.create_all(bind=engine)  # 保險起見：資料表不存在就自動建立，避免要求一定要先跑過 main.py


def main() -> None:
    db: Session = SessionLocal()
    try:
        pending = db.query(Photo).filter(Photo.is_embedded == False).all()  # noqa: E712
        if not pending:
            print("沒有待處理的照片，全部都算過特徵了。")
            return

        print(f"找到 {len(pending)} 張待處理照片（照片存在{'R2' if storage.USE_R2 else '本機'}）...")
        for photo in pending:
            if not storage.exists("uploads", photo.file_path):
                print(f"[跳過] 找不到檔案：{photo.file_path}")
                continue

            data = storage.read("uploads", photo.file_path)
            suffix = os.path.splitext(photo.file_path)[1] or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                vector = encode_image(tmp_path)
            finally:
                os.remove(tmp_path)

            vector_store.add_photo(
                photo_id=photo.id,
                product_id=photo.product_id,
                vector=vector,
                extra_metadata={"file_path": photo.file_path},
            )
            photo.is_embedded = True
            print(f"[OK] photo_id={photo.id} (product_id={photo.product_id}) 已算好特徵")

        db.commit()
        print(f"\n完成！Chroma 裡現在共有 {vector_store.count()} 筆向量。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
