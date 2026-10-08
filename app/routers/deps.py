"""所有业务写接口共用的会话请求保护。"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ServerSession
from app.services.auth import validate_csrf


def require_csrf_token(request: Request, db: Session = Depends(get_db)) -> None:
    """写接口必须携带当前会话的 X-CSRF-Token。

    与 web/web_routes.py 的页面写入口使用同一套会话契约；
    身份和角色检查由各接口另外复用 auth_contract 的依赖。
    """
    session_id = request.cookies.get("session_id")
    if session_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    session = db.get(ServerSession, session_id) if session_id is not None else None
    if session is None:
        raise HTTPException(status_code=401, detail="登录状态无效")
    validate_csrf(session, request.headers.get("X-CSRF-Token"))
