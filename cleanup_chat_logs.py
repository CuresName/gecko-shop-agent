"""
Phase 5：清掉過期的低信心對話照片
====================================
低信心對話會留一份客人上傳的照片（見 main.py 的 /chat），但設了 90 天保留期限
（models.ChatLog.photo_expires_at）——這支腳本負責把到期的照片檔案刪掉、把
photo_path 清空（文字紀錄本身繼續留著，只清照片）。

部署後建議排程定期跑（例如每天一次），本機開發時也可以手動跑。
"""

import os
from datetime import datetime

from database import SessionLocal
from models import ChatLog

LOG_PHOTO_DIR = os.environ.get("GECKO_SHOP_LOG_PHOTO_DIR", "static/chat_log_photos")


def main() -> None:
    db = SessionLocal()
    try:
        expired = (
            db.query(ChatLog)
            .filter(ChatLog.photo_path.isnot(None), ChatLog.photo_expires_at < datetime.utcnow())
            .all()
        )

        if not expired:
            print("沒有到期的照片，不用清。")
            return

        print(f"找到 {len(expired)} 筆到期的照片，開始清理...")
        for log in expired:
            path = os.path.join(LOG_PHOTO_DIR, log.photo_path)
            if os.path.exists(path):
                os.remove(path)
                print(f"[OK] 刪除 {path}（chat_log id={log.id}）")
            else:
                print(f"[跳過] 檔案本來就不在：{path}（chat_log id={log.id}）")
            log.photo_path = None
            log.photo_expires_at = None

        db.commit()
        print(f"\n完成！清掉了 {len(expired)} 筆過期照片。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
