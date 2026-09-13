from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountRole,
    BillingJob,
    Grade,
    Offering,
    Schedule,
    ServerSession,
    Student,
    Teacher,
)

from app.services.auth import create_account, invalidate_sessions

def generate_login_number(
    db: Session,
    role: AccountRole,
) -> str:
    year = str(date.today().year)

    if role == AccountRole.STUDENT:
        prefix = year
        last_number = db.scalar(
            select(func.max(Student.student_number))
            .where(Student.student_number.like(f"{prefix}%"))
        )

    elif role == AccountRole.TEACHER:
        prefix = f"T{year}"
        last_number = db.scalar(
            select(func.max(Teacher.teacher_number))
            .where(Teacher.teacher_number.like(f"{prefix}%"))
        )

    else:
        raise ValueError("只能为学生或教师生成登录编号")

    sequence = 1 if last_number is None else int(last_number[-4:]) + 1
    return f"{prefix}{sequence:04d}"


def create_student(
        
    db: Session,
    name: str,
    initial_password: str,
) -> Student:
    with db.begin():
        login_number = generate_login_number(
            db,
            AccountRole.STUDENT,
        )

        student = Student(
            student_number=login_number,
            name=name,
            active=True,
        )
        db.add(student)
        db.flush()  # 此时 student.id 已生成，但还没有提交

        create_account(
            db=db,
            login_number=login_number,
            password=initial_password,
            role=AccountRole.STUDENT,
            subject_id=student.id,
        )

    return student



def create_teacher(
    db: Session,
    name: str,
    department: str,
    initial_password: str,
) -> Teacher:
    with db.begin():
        login_number = generate_login_number(
            db,
            AccountRole.TEACHER,
        )

        teacher = Teacher(
            teacher_number=login_number,
            name=name,
            department=department,
            active=True,
        )
        db.add(teacher)

        # 先写入数据库，取得 teacher.id，但尚未提交事务
        db.flush()

        create_account(
            db=db,
            login_number=login_number,
            password=initial_password,
            role=AccountRole.TEACHER,
            subject_id=teacher.id,
        )

    return teacher


def list_students(
    db: Session,
    *,
    student_number: str | None = None,
    name: str | None = None,
    active: bool | None = None,
) -> list[Student]:
    """按可选条件查询学生；所有条件均为空时返回全部学生。"""
    stmt = select(Student)
    if student_number is not None:
        stmt = stmt.where(Student.student_number == student_number)
    if name is not None:
        stmt = stmt.where(Student.name.contains(name))
    if active is not None:
        stmt = stmt.where(Student.active == active)
    return list(db.scalars(stmt.order_by(Student.student_number)).all())


def list_teachers(
    db: Session,
    *,
    teacher_number: str | None = None,
    name: str | None = None,
    department: str | None = None,
    active: bool | None = None,
) -> list[Teacher]:
    """按工号、姓名、院系和启用状态查询教师。"""
    stmt = select(Teacher)
    if teacher_number is not None:
        stmt = stmt.where(Teacher.teacher_number == teacher_number)
    if name is not None:
        stmt = stmt.where(Teacher.name.contains(name))
    if department is not None:
        stmt = stmt.where(Teacher.department == department)
    if active is not None:
        stmt = stmt.where(Teacher.active == active)
    return list(db.scalars(stmt.order_by(Teacher.teacher_number)).all())


def get_student(db: Session, student_id: int) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise ValueError("学生不存在")
    return student


def get_teacher(db: Session, teacher_id: int) -> Teacher:
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise ValueError("教师不存在")
    return teacher


def update_student(
    db: Session,
    student_id: int,
    *,
    name: str | None = None,
    active: bool | None = None,
) -> Student:
    student = get_student(db, student_id)
    if name is not None:
        if not name.strip():
            raise ValueError("学生姓名不能为空")
        student.name = name.strip()
    if active is not None:
        student.active = active
    db.commit()
    db.refresh(student)
    return student


def update_teacher(
    db: Session,
    teacher_id: int,
    *,
    name: str | None = None,
    department: str | None = None,
    active: bool | None = None,
) -> Teacher:
    teacher = get_teacher(db, teacher_id)
    if name is not None:
        if not name.strip():
            raise ValueError("教师姓名不能为空")
        teacher.name = name.strip()
    if department is not None:
        teacher.department = department.strip()
    if active is not None:
        teacher.active = active
    db.commit()
    db.refresh(teacher)
    return teacher


def has_student_business_records(db: Session, student_id: int) -> bool:
    """软删除的选课方案同样属于历史，不能据此物理删除学生。"""
    return any(
        db.scalar(select(model.id).where(column == student_id).limit(1)) is not None
        for model, column in (
            (Schedule, Schedule.student_id),
            (Grade, Grade.student_id),
            (BillingJob, BillingJob.student_id),
        )
    )


def has_teacher_business_records(db: Session, teacher_id: int) -> bool:
    return db.scalar(
        select(Offering.id).where(Offering.teacher_id == teacher_id).limit(1)
    ) is not None


def _account_for_subject(db: Session, role: AccountRole, subject_id: int) -> Account | None:
    return db.scalar(
        select(Account).where(Account.role == role, Account.subject_id == subject_id)
    )


def _delete_account_and_sessions(db: Session, account: Account | None) -> None:
    if account is None:
        return
    for session in db.scalars(select(ServerSession).where(ServerSession.account_id == account.id)):
        db.delete(session)
    db.delete(account)


def delete_or_deactivate_student(db: Session, student_id: int) -> str:
    # 人员、账号与会话必须作为一个原子操作：全部成功才提交。
    with db.begin():
        student = get_student(db, student_id)
        account = _account_for_subject(db, AccountRole.STUDENT, student.id)

        if has_student_business_records(db, student.id):
            student.active = False
            if account is not None:
                account.active = False
                invalidate_sessions(db, account.id)
            return "deactivated"

        _delete_account_and_sessions(db, account)
        db.delete(student)
        return "deleted"


def delete_or_deactivate_teacher(db: Session, teacher_id: int) -> str:
    # 与学生删除保持相同的事务边界，避免留下半完成状态。
    with db.begin():
        teacher = get_teacher(db, teacher_id)
        account = _account_for_subject(db, AccountRole.TEACHER, teacher.id)

        if has_teacher_business_records(db, teacher.id):
            teacher.active = False
            if account is not None:
                account.active = False
                invalidate_sessions(db, account.id)
            return "deactivated"

        _delete_account_and_sessions(db, account)
        db.delete(teacher)
        return "deleted"
