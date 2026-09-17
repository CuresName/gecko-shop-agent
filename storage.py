"""
照片儲存抽換層：本機開發用本機磁碟，部署到 Railway 之後用 Cloudflare R2。
================================================================================
用 GECKO_SHOP_STORAGE_BACKEND 這個環境變數切換（不是看 R2_* 這幾個變數存不存在）——
這樣即使你的 .env 裡已經放了 R2 的憑證（部署/遷移用），本機開發預設還是繼續用本機磁碟，
不會因為 .env 裡有 R2 憑證就悄悄切換行為。只有明確設定
GECKO_SHOP_STORAGE_BACKEND=r2 才會真的走 R2。

呼叫端不用管現在是哪一種模式，一律用 namespace + filename 存取：
    namespace 是 "uploads"（商品照片）或 "chat_log_photos"（Phase 5 低信心對話照片），
    filename 是那個 uuid 檔名（不含資料夾路徑）。
"""

import os

STORAGE_BACKEND = os.environ.get("GECKO_SHOP_STORAGE_BACKEND", "local").lower()
USE_R2 = STORAGE_BACKEND == "r2"

LOCAL_DIRS = {
    "uploads": os.environ.get("GECKO_SHOP_UPLOAD_DIR", "static/uploads"),
    "chat_log_photos": os.environ.get("GECKO_SHOP_LOG_PHOTO_DIR", "static/chat_log_photos"),
}

if USE_R2:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
    R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
    R2_ENDPOINT_URL = os.environ["R2_ENDPOINT_URL"]
    R2_BUCKET_NAME = os.environ["R2_BUCKET_NAME"]

    _client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
else:
    for _dir in LOCAL_DIRS.values():
        os.makedirs(_dir, exist_ok=True)


def _r2_key(namespace: str, filename: str) -> str:
    return f"{namespace}/{filename}"


def _local_path(namespace: str, filename: str) -> str:
    return os.path.join(LOCAL_DIRS[namespace], filename)


def save(namespace: str, filename: str, data: bytes) -> None:
    if USE_R2:
        _client.put_object(Bucket=R2_BUCKET_NAME, Key=_r2_key(namespace, filename), Body=data)
    else:
        with open(_local_path(namespace, filename), "wb") as f:
            f.write(data)


def read(namespace: str, filename: str) -> bytes:
    if USE_R2:
        resp = _client.get_object(Bucket=R2_BUCKET_NAME, Key=_r2_key(namespace, filename))
        return resp["Body"].read()
    with open(_local_path(namespace, filename), "rb") as f:
        return f.read()


def delete(namespace: str, filename: str) -> None:
    if USE_R2:
        _client.delete_object(Bucket=R2_BUCKET_NAME, Key=_r2_key(namespace, filename))
    else:
        path = _local_path(namespace, filename)
        if os.path.exists(path):
            os.remove(path)


def exists(namespace: str, filename: str) -> bool:
    if USE_R2:
        try:
            _client.head_object(Bucket=R2_BUCKET_NAME, Key=_r2_key(namespace, filename))
            return True
        except ClientError:
            return False
    return os.path.exists(_local_path(namespace, filename))
