"""成员 3 的师生资料与账号生命周期练习，展示服务层如何组织业务步骤。

调用关系通常是：教务页面 -> HTTP 路由 -> 本模块 -> MemoryStore / auth。
本模块没有 Request 或当前操作者参数，因此不会自行检查教务权限或 CSRF；
正式路由必须先认证当前用户、要求 registrar 角色并验证写请求，再调用这些函数。

MemoryStore 仅在进程内保存 Python 对象；transaction() 用深拷贝模拟失败回滚，
没有数据库持久化、行锁或并发保护。接入项目时应复用成员 1 的 Student / Teacher
及数据库事务，并由真实业务表判断能否删除。本文件不实现选课、成绩、授课或账单。

编号要分清：student_id / teacher_id 是人员主体 ID；account_id 是登录账号 ID；
student_number / teacher_number 是展示并用于登录的编号。三者不能互相替代。
"""

from __future__ import annotations

from datetime import date

from learn.auth import create_account, hash_password, invalidate_sessions
from learn.models import Member3Error, MemoryStore, StudentProfile, TeacherProfile


def validate_person_profile(
    *,
    name: str,
    birth_date: date,
    social_security_number: str,
    department: str | None = None,
    graduation_date: date | None = None,
) -> None:
    """检查本示例采用的资料规则；通过时返回 None，不通过时抛 Member3Error。

    调用者应先把前端日期字符串解析为 date。这里仅检查资料的基本格式和时间关系，
    不判断操作人权限，也不验证社会保障号码的真实性或唯一性。
    """
    if not name.strip() or len(name.strip()) > 100:
        raise Member3Error("姓名不能为空且不能超过 100 个字符")
    if birth_date > date.today():
        raise Member3Error("出生日期不能晚于今天")
    if not social_security_number.strip():
        raise Member3Error("社会保障号码不能为空")
    if department is not None and not department.strip():
        raise Member3Error("教师院系不能为空")
    if graduation_date is not None and graduation_date <= birth_date:
        raise Member3Error("毕业日期必须晚于出生日期")


def generate_login_number(store: MemoryStore, role: str) -> str:
    """返回尚未使用的 S/T 加六位数字编号；此格式是学习示例的选择。

    当前函数只查找、不占用编号。内存中的顺序查找只能演示避免已有编号冲突；
    正式数据库还需要唯一约束及并发冲突处理，不能用它保证并发分配安全。
    """
    if role == "student":
        prefix, start = "S", store._next_student_id
    elif role == "teacher":
        prefix, start = "T", store._next_teacher_id
    else:
        raise Member3Error("只能为学生或教师分配编号")
    candidate_number = start
    while store.account_by_login(f"{prefix}{candidate_number:06d}"):
        candidate_number += 1
    return f"{prefix}{candidate_number:06d}"


def create_student(
    store: MemoryStore,
    *,
    name: str,
    birth_date: date,
    social_security_number: str,
    graduation_date: date,
    initial_password: str,
) -> StudentProfile:
    """校验学生资料、分配学号并建立关联账号，返回保存的 StudentProfile。

    initial_password 只交给 auth 生成哈希；账号默认要求首次改密。
    返回值是人员资料，不包含账号 ID 或会话；账号通过 (student, student.id) 关联。
    """
    validate_person_profile(
        name=name,
        birth_date=birth_date,
        social_security_number=social_security_number,
        graduation_date=graduation_date,
    )
    # 把“建资料”和“建账号”视为同一操作：例如密码校验失败时，资料和计数器也回滚。
    # 这是 MemoryStore 的快照演示，不是实际 SQL 事务。
    with store.transaction():
        number = generate_login_number(store, "student")
        student = StudentProfile(
            id=store._next_student_id,
            student_number=number,
            name=name.strip(),
            birth_date=birth_date,
            social_security_number=social_security_number.strip(),
            graduation_date=graduation_date,
        )
        store._next_student_id += 1
        store.students[student.id] = student
        create_account(
            store,
            role="student",
            subject_id=student.id,  # 传人员主体 ID；create_account 会另行分配账号 ID。
            login_number=number,
            initial_password=initial_password,
        )
        return student


def create_teacher(
    store: MemoryStore,
    *,
    name: str,
    birth_date: date,
    social_security_number: str,
    department: str,
    initial_password: str,
) -> TeacherProfile:
    """创建教师资料及首次改密账号，返回 TeacherProfile；院系在本例中不能为空。

    教师资料与学生资料分别编号，因此它们的数字 ID 可以相同，查找账号时要带角色。
    创建资料与账号同处于内存快照事务中，任一步骤失败便恢复仓库快照。
    """
    validate_person_profile(
        name=name,
        birth_date=birth_date,
        social_security_number=social_security_number,
        department=department,
    )
    with store.transaction():
        number = generate_login_number(store, "teacher")
        teacher = TeacherProfile(
            id=store._next_teacher_id,
            teacher_number=number,
            name=name.strip(),
            birth_date=birth_date,
            social_security_number=social_security_number.strip(),
            department=department.strip(),
        )
        store._next_teacher_id += 1
        store.teachers[teacher.id] = teacher
        create_account(
            store,
            role="teacher",
            subject_id=teacher.id,
            login_number=number,
            initial_password=initial_password,
        )
        return teacher


def list_students(
    store: MemoryStore, *, keyword: str = "", active: bool | None = None
) -> list[StudentProfile]:
    """按姓名/学号的子串和启用状态筛选，返回按主体 ID 排序的学生列表。

    active=None 表示包含启用和停用人员；True / False 表示只取对应状态。
    返回的是仓库中原对象的引用，不是脱敏后的 HTTP 响应，前端输出需另行选择字段。
    """
    key = keyword.strip().lower()
    return [
        student
        for student in sorted(store.students.values(), key=lambda item: item.id)
        if (not key or key in student.name.lower() or key in student.student_number.lower())
        and (active is None or student.active == active)
    ]


def list_teachers(
    store: MemoryStore,
    *,
    keyword: str = "",
    department: str = "",
    active: bool | None = None,
) -> list[TeacherProfile]:
    """按姓名/编号、院系子串、启用状态筛选教师，返回按主体 ID 排序的原对象。

    空关键词或空院系不限制该条件，active=None 不限制启用状态；当前没有分页。
    正式列表响应应只包含页面需要的字段，避免直接暴露社会保障号码等资料。
    """
    key, dept = keyword.strip().lower(), department.strip().lower()
    return [
        teacher
        for teacher in sorted(store.teachers.values(), key=lambda item: item.id)
        if (not key or key in teacher.name.lower() or key in teacher.teacher_number.lower())
        and (not dept or dept in teacher.department.lower())
        and (active is None or teacher.active == active)
    ]


def get_student(store: MemoryStore, student_id: int) -> StudentProfile:
    """按学生主体 ID 返回原资料对象；不存在时抛出带 404 状态的业务异常。"""
    student = store.students.get(student_id)
    if student is None:
        raise Member3Error("学生不存在", 404)
    return student


def get_teacher(store: MemoryStore, teacher_id: int) -> TeacherProfile:
    """按教师主体 ID 返回原资料对象；是否有权查看仍由调用者检查。"""
    teacher = store.teachers.get(teacher_id)
    if teacher is None:
        raise Member3Error("教师不存在", 404)
    return teacher


def update_student(
    store: MemoryStore,
    student_id: int,
    *,
    name: str,
    birth_date: date,
    social_security_number: str,
    graduation_date: date,
) -> StudentProfile:
    """校验后替换学生的可编辑资料，返回更新后的对象；未找到时抛 404。

    这是传入全部资料字段的更新操作，不是省略字段即保持不变的局部更新。
    学号、账号密码、启用状态均由其他操作管理。修改原对象会立即反映到内存仓库。
    """
    validate_person_profile(
        name=name,
        birth_date=birth_date,
        social_security_number=social_security_number,
        graduation_date=graduation_date,
    )
    student = get_student(store, student_id)
    student.name = name.strip()
    student.birth_date = birth_date
    student.social_security_number = social_security_number.strip()
    student.graduation_date = graduation_date
    return student


def update_teacher(
    store: MemoryStore,
    teacher_id: int,
    *,
    name: str,
    birth_date: date,
    social_security_number: str,
    department: str,
) -> TeacherProfile:
    """校验并替换教师可编辑资料，返回更新对象；保留教师编号、账号及启用状态。"""
    validate_person_profile(
        name=name,
        birth_date=birth_date,
        social_security_number=social_security_number,
        department=department,
    )
    teacher = get_teacher(store, teacher_id)
    teacher.name = name.strip()
    teacher.birth_date = birth_date
    teacher.social_security_number = social_security_number.strip()
    teacher.department = department.strip()
    return teacher


def has_student_business_records(store: MemoryStore, student_id: int) -> bool:
    """读取人工添加的学生历史标记；True 表示删除请求应改为停用。

    本例的 set 不会自动读取选课、成绩或账单；正式实现必须查询对应历史记录，
    并与成员 1、4 明确关联关系和事务边界，不能以此集合为空认定数据库没有历史。
    """
    return student_id in store.student_business_records


def has_teacher_business_records(store: MemoryStore, teacher_id: int) -> bool:
    """读取人工添加的教师历史标记；正式接入时需查询授课、成绩等实际业务关联。"""
    return teacher_id in store.teacher_business_records


def deactivate_account(store: MemoryStore, account_id: int) -> None:
    """停用指定账号及关联师生资料，撤销该账号所有会话；成功返回 None。

    这里接收账号 ID，不是学生/教师主体 ID。停用保留原资料和业务历史，
    会话通过 revoked_at 标记失效，后续会话检查会拒绝访问。
    """
    account = store.accounts.get(account_id)
    if account is None:
        raise Member3Error("账号不存在", 404)
    account.active = False
    if account.role == "student":
        get_student(store, account.subject_id).active = False
    elif account.role == "teacher":
        get_teacher(store, account.subject_id).active = False
    invalidate_sessions(store, account.id)


def set_account_status(store: MemoryStore, account_id: int, is_active: bool) -> None:
    """设置账号与师生资料的启用状态，成功返回 None。

    停用会撤销旧会话；重新启用只恢复 active，不清除会话的撤销标记，用户要重新登录。
    本例只维护启用布尔值，不表达在读、毕业、休学等尚需确认的业务状态字典。
    """
    account = store.accounts.get(account_id)
    if account is None:
        raise Member3Error("账号不存在", 404)
    if not is_active:
        deactivate_account(store, account_id)
        return
    account.active = True
    if account.role == "student":
        get_student(store, account.subject_id).active = True
    elif account.role == "teacher":
        get_teacher(store, account.subject_id).active = True


def reset_account_password(
    store: MemoryStore, account_id: int, initial_password: str
) -> None:
    """教务重置密码：写入新哈希、要求再次改密，并撤销所有旧会话。

    此函数不检查原密码，必须由已验证教务权限的调用者使用；与用户自行改密有区别。
    initial_password 仍需通过 auth 中的密码规则，失败会抛业务异常。
    """
    account = store.accounts.get(account_id)
    if account is None:
        raise Member3Error("账号不存在", 404)
    account.password_hash = hash_password(initial_password)
    account.must_change_password = True
    invalidate_sessions(store, account.id)


def _delete_account(store: MemoryStore, role: str, subject_id: int) -> None:
    """内部辅助函数：按角色和主体 ID 找账号，先撤销会话，再删除账号。

    不负责判断历史或删除师生资料；调用方先确定允许删除。失效会话仍留在内存仓库。
    """
    account = store.account_for_subject(role, subject_id)  # type: ignore[arg-type]
    if account is not None:
        invalidate_sessions(store, account.id)
        del store.accounts[account.id]


def delete_or_deactivate_student(store: MemoryStore, student_id: int) -> str:
    """处理教务删除请求，返回 deleted（物理删除）或 deactivated（停用保留）。

    先确认学生存在；有历史只停用账号和资料，无历史则一起删除账号与资料。
    返回值让路由/前端能明确告知最终采取的操作，不能一律显示“已删除”。
    """
    get_student(store, student_id)
    account = store.account_for_subject("student", student_id)
    # 历史数据归成员 1、4 的业务模块所有；此处只决定人员是否能删除，不清理这些记录。
    if has_student_business_records(store, student_id):
        if account is None:
            raise Member3Error("学生账号不存在")
        deactivate_account(store, account.id)
        return "deactivated"
    with store.transaction():
        _delete_account(store, "student", student_id)
        del store.students[student_id]
    return "deleted"


def delete_or_deactivate_teacher(store: MemoryStore, teacher_id: int) -> str:
    """按教师历史情况返回 deleted 或 deactivated，规则与学生删除一致。

    使用教师主体 ID 检查资料与历史，再转换为账号 ID 做停用或会话撤销。
    """
    get_teacher(store, teacher_id)
    account = store.account_for_subject("teacher", teacher_id)
    if has_teacher_business_records(store, teacher_id):
        if account is None:
            raise Member3Error("教师账号不存在")
        deactivate_account(store, account.id)
        return "deactivated"
    with store.transaction():
        _delete_account(store, "teacher", teacher_id)
        del store.teachers[teacher_id]
    return "deleted"


def init_registrar(
    store: MemoryStore, login_number: str, initial_password: str
):
    """供可信初始化流程创建教务账号，返回已有或新建的 Account。

    完全相同的编号已有教务账号时直接返回，不覆盖密码；若属于其他角色则抛 409。
    本例没有教务资料表，新账号暂用待分配的账号 ID 作为主体 ID；正式接入需明确教务
    主体的存储方式。此函数不是公开注册接口，也不应由未认证用户调用。
    """
    existing = store.account_by_login(login_number)
    if existing is not None:
        if existing.role != "registrar":
            raise Member3Error("初始化编号已被其他角色使用", 409)
        return existing
    return create_account(
        store,
        role="registrar",
        subject_id=store._next_account_id,
        login_number=login_number,
        initial_password=initial_password,
    )
