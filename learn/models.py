"""学习模型：用普通 Python 对象表示账号、人员和会话，不会创建数据库表。

先读 CurrentUser/Account，再看 MemoryStore；它相当于各服务函数共同操作的记事本。
这里只复现正式接口的身份结构，没有导入或替换 app/ 中的任何模型。
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Iterator, Literal


# Literal 告诉类型检查器允许哪些字符串；不会自动验证运行时输入。
Role = Literal["student", "teacher", "registrar"]


class Member3Error(Exception):
    """学习代码的业务异常；接入 FastAPI 时转换为 {"detail": message}。"""

    def __init__(self, message: str, status_code: int = 400):
        """携带提示和 HTTP 状态码；这里只抛异常，不会自动生成 HTTP 响应。"""
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class CurrentUser:
    """与正式契约同字段的学习类型；frozen=True 防止创建后改写身份字段。

    它和 app.auth_contract.CurrentUser 是两个 Python 类；正式接入时复用后者。
    """

    role: Role
    subject_id: int


@dataclass
class Account:
    """登录资料；账号 ID 与学生/教师的主体 ID 是两种编号。"""

    id: int
    login_number: str
    role: Role
    subject_id: int  # 学生指向 StudentProfile.id，教师指向 TeacherProfile.id。
    password_hash: str
    active: bool = True
    must_change_password: bool = True  # 密码验证成功不代表已经允许进入业务。


@dataclass
class ServerSession:
    """一次登录的服务端记录；不包含浏览器 Cookie 的设置逻辑。"""

    id: str
    account_id: int
    csrf_token: str  # 请求防伪令牌，与用于识别登录会话的 id 用途不同。
    expires_at: datetime
    revoked_at: datetime | None = None  # None 表示未撤销；还需检查 expires_at。


@dataclass
class StudentProfile:
    """学习用学生资料；不是 app.models.Student，尚未实现资料状态枚举。"""

    id: int
    student_number: str
    name: str
    birth_date: date
    social_security_number: str
    graduation_date: date
    active: bool = True


@dataclass
class TeacherProfile:
    """学习用教师资料；active 仅代表启用状态，不能替代全部资料状态。"""

    id: int
    teacher_number: str
    name: str
    birth_date: date
    social_security_number: str
    department: str
    active: bool = True


@dataclass
class MemoryStore:
    """用 dict/set 模拟存储，进程结束就丢失，不能跨进程共享或防止并发冲突。

    dataclass 自动生成构造方法；default_factory 让每个 store 拥有独立的容器。
    正式实现需替换为 SQLAlchemy 查询、真实事务和数据库唯一约束。
    """

    accounts: dict[int, Account] = field(default_factory=dict)
    sessions: dict[str, ServerSession] = field(default_factory=dict)
    students: dict[int, StudentProfile] = field(default_factory=dict)
    teachers: dict[int, TeacherProfile] = field(default_factory=dict)
    # 这两个集合仅由测试人工标记“存在历史”；不会查询成员 1/4 的业务表。
    student_business_records: set[int] = field(default_factory=set)
    teacher_business_records: set[int] = field(default_factory=set)
    _next_account_id: int = 1
    _next_student_id: int = 1
    _next_teacher_id: int = 1

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """配合 with 使用：执行前深拷贝，出错时恢复仓库并继续抛出异常。

        这是单进程教学回滚，不是数据库事务，也没有锁或事务隔离。
        回滚后仓库中的对象已被替换；外部旧变量仍可能指向修改过的旧对象，需重新查询。
        """
        snapshot = deepcopy(self.__dict__)
        try:
            # 控制权交给 with 内部代码；正常结束就保留本次修改。
            yield
        except Exception:
            # 同时恢复字典内容及计数器，演示“资料和账号不能只成功一半”。
            self.__dict__.clear()
            self.__dict__.update(snapshot)
            raise

    def account_by_login(self, login_number: str) -> Account | None:
        """按登录编号找第一个账号，未找到返回 None；next 遍历的仍是内存对象。"""
        return next(
            (a for a in self.accounts.values() if a.login_number == login_number),
            None,
        )

    def account_for_subject(self, role: Role, subject_id: int) -> Account | None:
        """同时按角色和主体 ID 查询，避免把学生 1 误认为教师 1。"""
        return next(
            (
                a
                for a in self.accounts.values()
                if a.role == role and a.subject_id == subject_id
            ),
            None,
        )

    def person_is_active(self, account: Account) -> bool:
        """检查关联人员是否存在且启用；教务示例没有独立资料表。

        这里不检查 account.active，调用方必须同时检查账号和人员两层状态。
        """
        if account.role == "student":
            person = self.students.get(account.subject_id)
            return person is not None and person.active
        if account.role == "teacher":
            person = self.teachers.get(account.subject_id)
            return person is not None and person.active
        return True

    @staticmethod
    def now() -> datetime:
        """统一返回带 UTC 时区的时间，供会话到期和撤销比较使用。"""
        return datetime.now(UTC)
