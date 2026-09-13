"""初始化可重复执行的答辩演示数据。

从项目根目录运行：
    ./.venv/Scripts/python.exe -m scripts.seed_demo_data

该脚本只补充缺失的演示记录，不会删除或覆盖已有数据。
"""

import argparse
import getpass
import os
from collections.abc import Callable
from decimal import Decimal
from random import Random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Account, AccountRole, Course, Student, Teacher
from app.services.personnel import create_student, create_teacher, init_registrar


INITIAL_USER_PASSWORD = "Initial123"
DEMO_STUDENT_COUNT = 10
DEMO_TEACHER_COUNT = 10
_SURNAMES = ("王", "李", "张", "刘", "陈", "杨", "黄", "赵", "吴", "周", "徐", "孙")
_GIVEN_NAMES = (
    "子轩", "雨桐", "浩然", "思涵", "宇航", "欣怡", "俊杰", "若曦", "嘉铭", "语嫣",
    "梓涵", "博文", "依诺", "泽宇", "诗涵", "晨曦", "昊然", "可欣", "逸凡", "梦琪",
)
_DEPARTMENTS = ("计算机学院", "软件学院", "信息工程学院", "人工智能学院")


def _random_demo_names(count: int, *, seed: int) -> tuple[str, ...]:
    """生成固定但自然的演示姓名，保证脚本多次执行仍能识别同一批数据。"""
    candidates = tuple(f"{surname}{given_name}" for surname in _SURNAMES for given_name in _GIVEN_NAMES)
    return tuple(Random(seed).sample(candidates, count))


DEMO_STUDENTS = _random_demo_names(DEMO_STUDENT_COUNT, seed=20260913)
_teacher_names = _random_demo_names(DEMO_TEACHER_COUNT, seed=20260914)
_department_random = Random(20260915)
DEMO_TEACHERS = tuple((name, _department_random.choice(_DEPARTMENTS)) for name in _teacher_names)
DEMO_COURSES = (
    ("DEMO101", "软件工程导论", "答辩演示课程：软件工程基础。", Decimal("120.00")),
    ("DEMO102", "数据库系统", "答辩演示课程：关系数据库设计。", Decimal("100.00")),
    ("DEMO103", "Python 程序设计", "答辩演示课程：Python 基础。", Decimal("80.00")),
)


def seed_demo_data(
    registrar_password: str,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, int]:
    """补充 10 名学生、10 名教师及课程演示数据，返回本次新建数量。

    学生、教师的登录编号由 ``create_student`` / ``create_teacher`` 按年份生成；
    所有新建师生的初始密码均为 ``Initial123``，首次登录必须修改。
    """
    created = {"registrar": 0, "students": 0, "teachers": 0, "courses": 0}
    with session_factory() as db:
        # init_registrar 本身可重复执行；用角色查询判断是否为本次创建。
        registrar_exists = db.scalar(
            select(Account.id).where(Account.role == AccountRole.REGISTRAR)
        ) is not None
        init_registrar(db, "registrar", registrar_password)
        created["registrar"] = 0 if registrar_exists else 1

        for name in DEMO_STUDENTS:
            if db.scalar(select(Student.id).where(Student.name == name)) is None:
                create_student(db, name, INITIAL_USER_PASSWORD)
                created["students"] += 1

        for name, department in DEMO_TEACHERS:
            teacher_exists = db.scalar(
                select(Teacher.id).where(
                    Teacher.name == name, Teacher.department == department
                )
            )
            if teacher_exists is None:
                create_teacher(db, name, department, INITIAL_USER_PASSWORD)
                created["teachers"] += 1

        for code, name, description, fee in DEMO_COURSES:
            if db.scalar(select(Course.id).where(Course.code == code)) is None:
                db.add(Course(code=code, name=name, description=description, fee=fee))
                created["courses"] += 1
        db.commit()

    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化答辩演示师生、账号与课程数据")
    parser.add_argument("--registrar-password", help="教务账号初始密码；优先级高于环境变量")
    args = parser.parse_args()

    password = args.registrar_password or os.getenv("INITIAL_REGISTRAR_PASSWORD")
    if password is None:
        password = getpass.getpass("请输入教务初始密码：")

    created = seed_demo_data(password)
    print("演示数据初始化完成：" + "，".join(f"{key}={value}" for key, value in created.items()))
    print("新建师生账号的初始密码为 Initial123；首次登录后必须修改密码。")


if __name__ == "__main__":
    main()
