"""用三个独立场景阅读成员 3 服务层：首次改密、CSRF/停用、删除与历史保留。

每个测试新建 MemoryStore，因此数据不会跨测试共享，也不会访问真实数据库。
这是学习函数的单元测试；没有 HTTP 路由、浏览器 Cookie 或成员 1/4 的业务联调。
测试直接调用服务函数，不能据此认定教务角色鉴权或前端接入已经完成。
运行方式：在仓库根目录执行 python -m pytest learn/test_member3_functions.py。
"""

from datetime import date

import pytest

from learn.auth import (
    authenticate_user,
    change_password,
    create_session,
    current_user,
    get_session_account,
    validate_csrf,
)
from learn.models import Member3Error, MemoryStore
from learn.personnel import (
    create_student,
    delete_or_deactivate_student,
    set_account_status,
)


def create_example_student(store: MemoryStore):
    """用虚构资料建立学生及首次改密账号，返回学生资料，供各测试复用。"""
    return create_student(
        store,
        name="张三",
        birth_date=date(2005, 1, 2),
        social_security_number="DEMO-SSN-001",
        graduation_date=date(2028, 6, 30),
        initial_password="Start1234",
    )


def test_first_login_change_password_and_old_session_revocation():
    """验证初始密码可登录，但必须改密；改密撤销旧会话，重新登录后身份有效。"""
    store = MemoryStore()
    student = create_example_student(store)
    account = authenticate_user(store, student.student_number, "Start1234")
    session = create_session(store, account)

    # pytest.raises 表示“这里应该失败”；未抛异常或提示不匹配，测试都会失败。
    # 获得登录会话不等于获准访问业务接口：首次改密标记会继续阻止 current_user。
    with pytest.raises(Member3Error, match="必须先修改密码"):
        current_user(store, session.id)

    change_password(store, session.id, "Start1234", "Changed5678")
    with pytest.raises(Member3Error, match="请先登录"):
        get_session_account(store, session.id)

    new_account = authenticate_user(store, student.student_number, "Changed5678")
    new_session = create_session(store, new_account)
    # 后续选课等业务需要学生主体 ID，而不是账号 ID，也不是展示给用户的学号字符串。
    assert current_user(store, new_session.id).subject_id == student.id


def test_csrf_and_deactivation_reject_existing_session():
    """验证错误 CSRF 令牌被拒绝，账号停用后已有会话也不能继续使用。"""
    store = MemoryStore()
    student = create_example_student(store)
    account = authenticate_user(store, student.student_number, "Start1234")
    session = create_session(store, account)

    # 直接传入令牌只验证学习函数；浏览器从何处取得令牌、HTTP 层如何传递需要另行接入。
    validate_csrf(store, session.id, session.csrf_token)
    with pytest.raises(Member3Error, match="请求校验失败"):
        validate_csrf(store, session.id, "wrong")

    # 此 API 使用账号 ID。allow_password_change=True 只放行首次改密限制，
    # 不会放行已撤销的会话，因此停用后仍应报“请先登录”。
    set_account_status(store, account.id, False)
    with pytest.raises(Member3Error, match="请先登录"):
        get_session_account(store, session.id, allow_password_change=True)


def test_delete_without_history_but_deactivate_with_history():
    """验证无历史删除，有历史保留资料并停用；这里的历史由测试人工标记。"""
    store = MemoryStore()
    first = create_example_student(store)
    assert delete_or_deactivate_student(store, first.id) == "deleted"
    assert first.id not in store.students

    second = create_example_student(store)
    # 这个 set 仅表示“假定存在历史”，并未真实创建选课、成绩或账单记录。
    # 因此测试证明分支规则正确，不代表真实数据库历史查询和并发删除已经安全。
    store.student_business_records.add(second.id)
    assert delete_or_deactivate_student(store, second.id) == "deactivated"
    assert second.id in store.students
    assert not store.students[second.id].active
