"""
部署上線前要補的兩個防護：
1. 後台密碼保護：`/admin.html` 跟後面所有商品管理/覆核佇列 API，部署到公開網址
   之後不能讓任何人都能改價格/庫存——用最簡單的 HTTP Basic Auth 擋，不用做完整會員系統。
2. `/chat` 頻率限制：這個聊天室之後會公開嵌在網站上，每次呼叫都要花 Anthropic
   API 的錢，加一個很陽春的 per-IP 頻率限制，擋掉最基本的洗版/機器人濫用。

兩者都設計成「沒設定環境變數就不擋」，本機開發不用管這兩件事，只有部署環境
設好對應的環境變數才會生效。
"""

import base64
import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# ---------- 後台密碼保護 ----------

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

# 只有這幾個路徑是客人會用到的，其他一律要通過驗證——
# 包含 /admin.html、/products*、/chat_logs*、/search/image、/docs 等等。
PUBLIC_PATHS = {"/", "/chat"}

# 商品照片是客人聊天時會直接看到的東西，所以整個 /media/uploads/ 底下都公開；
# /media/chat_log_photos/ 是客人自己上傳、給店家覆核用的私密照片，繼續要密碼保護。
PUBLIC_PATH_PREFIXES = ("/media/uploads/",)


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if path.startswith(PUBLIC_PATH_PREFIXES):
        return True
    # 首頁聊天室本身不需要額外的靜態檔案，但保險起見 favicon 這種瀏覽器自動要的東西不擋
    if path == "/favicon.ico":
        return True
    return False


class AdminBasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ADMIN_USERNAME or not ADMIN_PASSWORD:
            return await call_next(request)  # 沒設定帳密，本機開發預設不擋

        if _is_public_path(request.url.path):
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[len("Basic "):]).decode("utf-8")
                username, _, password = decoded.partition(":")
            except Exception:
                username, password = "", ""

            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                return await call_next(request)

        return Response(
            content="需要登入後台帳密才能繼續",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="gecko-shop-admin"'},
        )


# ---------- /chat 頻率限制 ----------

RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("GECKO_SHOP_CHAT_RATE_LIMIT", "10"))
RATE_LIMIT_WINDOW_SECONDS = 60
_rate_limit_enabled = os.environ.get("GECKO_SHOP_ENABLE_RATE_LIMIT", "").lower() in ("1", "true", "yes")

_request_log: dict[str, list[float]] = defaultdict(list)


class ChatRateLimitMiddleware(BaseHTTPMiddleware):
    """很陽春的 per-IP 滑動視窗限制，擋掉最基本的洗版——不是為了防真正的 DDoS，
    只是不想讓 Anthropic API 費用被隨便打爆。單一伺服器實例夠用，之後真的要多台
    伺服器再換成 Redis 之類的共用儲存。
    """

    async def dispatch(self, request: Request, call_next):
        if not _rate_limit_enabled or request.url.path != "/chat":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW_SECONDS

        recent = [t for t in _request_log[client_ip] if t > window_start]
        if len(recent) >= RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "問太快了，休息一下再試一次喔"},
            )

        recent.append(now)
        _request_log[client_ip] = recent
        return await call_next(request)
