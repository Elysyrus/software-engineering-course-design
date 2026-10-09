"""成员 6 验收数据准备：在 seed_final_demo 之后补充 2027-SPRING 验收学期的教师授课资格。

前置：
1. 已执行 alembic upgrade head 与 python -m scripts.seed_final_demo；
2. 目录模拟服务以 MOCK_CATALOG_FILE=本目录/acceptance_catalog.json 启动；
3. 已执行 python -m scripts.sync_catalog 导入 2027-SPRING。

系统没有维护授课资格的页面（资格由教务在系统外确定），因此这里直接写入
teacher_qualifications 表。本脚本只追加资格记录，不修改选课、成绩或账号数据。

运行（项目根目录）：
    python "docs/测试与交付/验收工具/setup_acceptance_data.py"
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Course, Teacher, TeacherQualification  # noqa: E402

# 教师工号 -> 具备资格的验收课程。T009 故意不给任何验收课程资格，用于“无资格不能认领”。
QUALIFICATIONS = {
    "T001": ["ACC101", "ACC108"],
    "T002": ["ACC102", "ACC108"],
    "T003": ["ACC103"],
    "T004": ["ACC104"],
    "T005": ["ACC105"],
    "T006": ["ACC107"],
    "T007": ["ACC109"],
    "T008": ["ACC110"],
    "T010": ["ACC106"],
}


def main() -> None:
    added = 0
    with SessionLocal() as db:
        for teacher_number, course_codes in QUALIFICATIONS.items():
            teacher = db.scalar(
                select(Teacher).where(Teacher.teacher_number == teacher_number)
            )
            if teacher is None:
                raise SystemExit(f"缺少教师 {teacher_number}，请先运行 seed_final_demo")
            for code in course_codes:
                course = db.scalar(select(Course).where(Course.code == code))
                if course is None:
                    raise SystemExit(f"缺少课程 {code}，请先运行 sync_catalog")
                exists = db.scalar(
                    select(TeacherQualification.id).where(
                        TeacherQualification.teacher_id == teacher.id,
                        TeacherQualification.course_id == course.id,
                    )
                )
                if exists is None:
                    db.add(
                        TeacherQualification(teacher_id=teacher.id, course_id=course.id)
                    )
                    added += 1
        db.commit()
    print(f"已补充授课资格 {added} 条")


if __name__ == "__main__":
    main()
