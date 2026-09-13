"""认证规则的函数示例：输入普通对象，返回对象或抛 Member3Error。

本模块没有 FastAPI 路由，不会读取/设置 Cookie，也不会自动保护现有页面。
业务写请求应由调用层依次完成会话检查、CSRF 检查、角色授权，然后调用服务。
"""

from __future__ import annotations

import hmac
import secrets
from datetime import timedelta

from pwdlib import PasswordHash

from learn.models import (
    Account,
    CurrentUser,
    Member3Error,
    MemoryStore,
    Role,
    ServerSession,
)


# 密码库负责盐和哈希格式；这不是可解密的密码加密，也不是会话令牌生成器。
password_hasher = PasswordHash.recommended()


def validate_password(password: str) -> None:
    """检查示例密码规则，不合格就抛异常；长度/字符策略尚非正式接口约定。

    isalpha/isdigit 使用 Python 的 Unicode 分类，不限于 ASCII 英文和数字。
    """
    if len(password) < 8 or not any(c.isalpha() for c in password) or not any(
        c.isdigit() for c in password
    ):
        raise Member3Error("密码至少 8 位，并同时包含字母和数字")


def hash_password(password: str) -> str:
    """先验证新密码，再返回可保存的哈希字符串；不保存明文。"""
    validate_password(password)
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证已保存的哈希，返回是否匹配；不以当前新密码策略拒绝旧密码。

    期望 password_hash 来自密码库；存储内容损坏时，库异常尚未转换为 Member3Error。
    """
    return password_hasher.verify(password, password_hash)


def create_account(
    store: MemoryStore,
    *,
    role: Role,
    subject_id: int,
    login_number: str,
    initial_password: str,
) -> Account:
    """建立账号并返回存入仓库的对象，调用方传入已存在的人员主体 ID。

    * 后面的参数必须用名称传递。该函数不检查当前操作者是否为教务，
    必须由经过授权的创建/初始化流程调用；它本身也没有开启事务。
    """
    login_number = login_number.strip()
    if not login_number:
        raise Member3Error("登录编号不能为空")
    if store.account_by_login(login_number):
        raise Member3Error("登录编号已存在", 409)
    if role != "registrar" and not (
        (role == "student" and subject_id in store.students)
        or (role == "teacher" and subject_id in store.teachers)
    ):
        raise Member3Error("账号对应的人员资料不存在")

    # _next_account_id 为学习计数器。正式数据库还要约束登录编号及人员映射唯一性。
    account = Account(
        id=store._next_account_id,
        login_number=login_number,
        role=role,
        subject_id=subject_id,
        password_hash=hash_password(initial_password),
    )
    store._next_account_id += 1
    store.accounts[account.id] = account
    return account


def authenticate_user(
    store: MemoryStore, login_number: str, password: str
) -> Account:
    """校验登录凭据和启用状态，返回账号；允许尚未首次改密的账号登录。

    是否能进入选课等业务由 get_session_account 再检查，本函数不创建会话。
    """
    account = store.account_by_login(login_number.strip())
    if account is None or not verify_password(password, account.password_hash):
        raise Member3Error("登录编号或密码错误", 401)
    if not account.active or not store.person_is_active(account):
        raise Member3Error("账号已停用", 403)
    return account


def create_session(
    store: MemoryStore, account: Account, *, lifetime_minutes: int = 60
) -> ServerSession:
    """为已经验证的账号创建会话，默认有效 60 分钟，返回服务器记录。

    调用前先 authenticate_user；本函数不会重新验证密码/启用状态。
    Cookie 写入、有效期参数校验和会话持久化均需在正式接入时补齐。
    """
    session = ServerSession(
        # 两次独立随机生成：id 标识登录会话，csrf_token 用来验证修改请求。
        id=secrets.token_urlsafe(32),
        account_id=account.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=store.now() + timedelta(minutes=lifetime_minutes),
    )
    store.sessions[session.id] = session
    return session


def get_session_account(
    store: MemoryStore,
    session_id: str | None,
    *,
    allow_password_change: bool = False,
) -> Account:
    """每次请求按会话重新取得账号，拒绝到期、撤销或已停用的身份。

    allow_password_change=True 仅供改密等受限入口内部指定，不能交给客户端决定；
    它只放宽首次改密检查，不跳过会话和账号状态检查。
    """
    session = store.sessions.get(session_id or "")
    if session is None or session.revoked_at is not None:
        raise Member3Error("请先登录", 401)
    if session.expires_at <= store.now():
        raise Member3Error("登录已过期，请重新登录", 401)

    # 从仓库重新检查状态；不能仅依赖登录时保存在页面里的角色和 active。
    account = store.accounts.get(session.account_id)
    if account is None or not account.active or not store.person_is_active(account):
        raise Member3Error("账号已停用或不存在", 403)
    if account.must_change_password and not allow_password_change:
        raise Member3Error("首次登录必须先修改密码", 403)
    return account


def current_user(store: MemoryStore, session_id: str | None) -> CurrentUser:
    """供业务入口取得不可变身份；返回主体 ID，不是会话 ID 或账号 ID。"""
    account = get_session_account(store, session_id)
    return CurrentUser(role=account.role, subject_id=account.subject_id)


def require_student(user: CurrentUser) -> int:
    """检查可信身份的学生角色并返回学生 ID；不检查某份方案的归属。"""
    if user.role != "student":
        raise Member3Error("仅学生可执行此操作", 403)
    return user.subject_id


def require_teacher(user: CurrentUser) -> int:
    """检查教师角色并返回教师 ID；班次、成绩归属仍由成员 4 检查。"""
    if user.role != "teacher":
        raise Member3Error("仅教师可执行此操作", 403)
    return user.subject_id


def require_registrar(user: CurrentUser) -> int:
    """检查教务角色；user 必须来自认证结果，不能由请求体自行构造。"""
    if user.role != "registrar":
        raise Member3Error("仅教务人员可执行此操作", 403)
    return user.subject_id


def invalidate_sessions(store: MemoryStore, account_id: int) -> None:
    """按账号 ID 标记全部旧会话撤销；保留记录但禁止再次使用。"""
    now = store.now()
    for session in store.sessions.values():
        if session.account_id == account_id and session.revoked_at is None:
            session.revoked_at = now


def change_password(
    store: MemoryStore,
    session_id: str,
    old_password: str,
    new_password: str,
) -> None:
    """完成首次或后续改密，原地更新账号并撤销包括当前会话在内的旧会话。

    调用层还须先验证 CSRF；这里不会自动调用 validate_csrf，也没有数据库事务。
    """
    account = get_session_account(store, session_id, allow_password_change=True)
    if not verify_password(old_password, account.password_hash):
        raise Member3Error("原密码错误", 400)
    if old_password == new_password:
        raise Member3Error("新密码不能与原密码相同")
    # 右侧先完成校验和哈希，再赋值；新密码不合格时不会替换旧哈希。
    account.password_hash = hash_password(new_password)
    account.must_change_password = False
    invalidate_sessions(store, account.id)


def logout(store: MemoryStore, session_id: str | None) -> None:
    """撤销指定会话；重复退出或不存在时不报错。HTTP 层另行清除 Cookie。"""
    session = store.sessions.get(session_id or "")
    if session is not None and session.revoked_at is None:
        session.revoked_at = store.now()


def validate_csrf(
    store: MemoryStore, session_id: str | None, submitted_token: str | None
) -> None:
    """比较提交令牌和服务端令牌，不匹配时抛 403，不返回业务数据。

    只检查存在、撤销及令牌；不会检查到期/账号状态/角色，须搭配会话认证使用。
    受信页面须能取得自己的 CSRF 令牌再提交；它与 HttpOnly 会话 Cookie 不同。
    """
    session = store.sessions.get(session_id or "")
    if (
        session is None
        or session.revoked_at is not None
        or not submitted_token
        # compare_digest 比较令牌以减少时序差异，不是在这里重新计算密码哈希。
        or not hmac.compare_digest(session.csrf_token, submitted_token)
    ):
        raise Member3Error("请求校验失败，请刷新页面后重试", 403)
