"""成员 4 教师授课与学生成绩真实接口。

包含两套路径，都调用同一份服务实现：

- ``/api/v1/teacher/offerings/...``：REST 风格接口，字段直接对应数据模型；
- ``/api/v1/teacher/claimable-sections`` 等：页面兼容层，字段与成员 2 模板
  原先调用的模拟接口保持一致（成员 2 的模拟实现已从 web/web_routes.py 移除）。
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_contract import current_user, require_student, require_teacher
from app.database import get_db
from app.errors import BusinessError
from app.models import Semester, SemesterStatus
from app.routers.deps import require_csrf_token
from app.services import catalog, teaching

router = APIRouter(prefix="/api/v1", tags=["成员4-教学"])

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class GradeEntryInput(BaseModel):
    student_id: int
    # 空字符串与 null 都表示留空成绩
    value: str | None = None


class SaveGradesRequest(BaseModel):
    grades: list[GradeEntryInput] = Field(default_factory=list, max_length=200)


class SemesterItem(BaseModel):
    semester_id: int
    code: str
    status: str
    starts_on: str
    ends_on: str


class SemesterListResponse(BaseModel):
    semesters: list[SemesterItem]


@router.get("/semesters", response_model=SemesterListResponse)
def list_semesters(_user=Depends(current_user), db: Session = Depends(get_db)):
    """所有已登录角色都可读的学期列表，供页面选择学期。"""
    semesters = db.scalars(
        select(Semester).order_by(Semester.starts_on.desc(), Semester.id.desc())
    ).all()
    return SemesterListResponse(
        semesters=[
            SemesterItem(
                semester_id=semester.id,
                code=semester.code,
                status=semester.status.value,
                starts_on=semester.starts_on.isoformat(),
                ends_on=semester.ends_on.isoformat(),
            )
            for semester in semesters
        ]
    )


@router.get("/teacher/me/qualified-courses")
def get_qualified_courses(
    teacher_id: int = Depends(require_teacher), db: Session = Depends(get_db)
):
    """当前教师具备授课资格的课程。"""
    courses = teaching.list_qualified_courses(db, teacher_id=teacher_id)
    return {
        "courses": [
            {
                "course_id": course.id,
                "course_code": course.code,
                "course_name": course.name,
                "fee": str(course.fee),
            }
            for course in courses
        ]
    }


@router.get("/teacher/offerings/claimable")
def list_claimable_offerings(
    semester_id: int | None = Query(
        default=None, ge=1, description="学期 id；省略时使用当前开放学期"
    ),
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """当前教师可认领的班次；不传学期时使用当前开放学期。"""
    target_semester_id = semester_id or _default_semester_id(db)
    if target_semester_id is None:
        return {"semester_id": None, "offerings": []}
    return {
        "semester_id": target_semester_id,
        "offerings": _claimable_from_catalog(
            db, teacher_id=teacher_id, semester_id=target_semester_id
        ),
    }


@router.get("/teacher/offerings/mine")
def list_my_offerings(
    semester_id: int | None = Query(default=None, ge=1),
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """当前教师自己的授课列表。"""
    return {
        "offerings": teaching.list_my_offerings(
            db, teacher_id=teacher_id, semester_id=semester_id
        )
    }


@router.get("/teacher/gradable-sections")
def list_gradable_sections(
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """上一已完成学期中本人可以录入成绩的班次，供成绩录入页默认选择。"""
    return teaching.list_gradable_offerings(db, teacher_id=teacher_id)


@router.post(
    "/teacher/offerings/{offering_id}/claim",
    dependencies=[Depends(require_csrf_token)],
)
def claim_offering(
    offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    offering = teaching.claim_offering(
        db, teacher_id=teacher_id, offering_id=offering_id
    )
    return {
        "message": "认领成功",
        "offering_id": offering.id,
        "teacher_id": offering.teacher_id,
    }


@router.post(
    "/teacher/offerings/{offering_id}/release",
    dependencies=[Depends(require_csrf_token)],
)
def release_offering(
    offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    offering = teaching.release_offering(
        db, teacher_id=teacher_id, offering_id=offering_id
    )
    return {
        "message": "已取消授课",
        "offering_id": offering.id,
        "teacher_id": offering.teacher_id,
    }


@router.get("/teacher/offerings/{offering_id}/roster")
def get_offering_roster(
    offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """所教班次的正式学生名单。"""
    return teaching.get_offering_roster(
        db, teacher_id=teacher_id, offering_id=offering_id
    )


@router.get("/teacher/offerings/{offering_id}/grades")
def get_offering_grades(
    offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """班次成绩单：正式名单与当前成绩。"""
    return teaching.list_offering_grades(
        db, teacher_id=teacher_id, offering_id=offering_id
    )


@router.get("/teacher/offerings/{offering_id}/grade-changes")
def get_offering_grade_changes(
    offering_id: int,
    student_id: int | None = Query(default=None, ge=1),
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """班次成绩变更记录；可按学生过滤。"""
    return teaching.list_grade_changes(
        db,
        teacher_id=teacher_id,
        offering_id=offering_id,
        student_id=student_id,
    )


@router.put(
    "/teacher/offerings/{offering_id}/grades",
    dependencies=[Depends(require_csrf_token)],
)
def save_offering_grades(
    offering_id: int,
    body: SaveGradesRequest,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """录入、修改或留空成绩。"""
    return teaching.save_offering_grades(
        db,
        teacher_id=teacher_id,
        offering_id=offering_id,
        entries=[
            {"student_id": item.student_id, "value": item.value} for item in body.grades
        ],
    )


@router.get("/student/me/grades")
def get_my_grades(
    semester_id: int | None = Query(default=None, ge=1),
    student_id: int = Depends(require_student),
    db: Session = Depends(get_db),
):
    """学生查看自己的成绩。"""
    return teaching.list_student_grades(
        db, student_id=student_id, semester_id=semester_id
    )


# ---------------------------------------------------------------------------
# 页面兼容层：成员 2 的模板原先调用这些路径上的模拟接口，现在改为真实数据。
# 字段名保持模板既有用法，只在模拟接口虚构了模型里没有的字段处做了调整。
# ---------------------------------------------------------------------------


def _format_schedule(slots: list[dict]) -> str:
    return "、".join(
        f"{WEEKDAY_NAMES[slot['weekday'] - 1]} {slot['start_period']}-{slot['end_period']}节"
        for slot in slots
    )


def _format_moment(value) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else None


def _semester_codes(db: Session) -> dict[int, str]:
    return {
        semester.id: semester.code for semester in db.scalars(select(Semester)).all()
    }


def _default_semester_id(db: Session) -> int | None:
    """页面未指定学期时使用当前开放学期；没有开放学期则退回最近一个学期。"""
    semester = db.scalar(
        select(Semester)
        .where(Semester.status == SemesterStatus.OPEN)
        .order_by(Semester.starts_on.desc(), Semester.id.desc())
        .limit(1)
    )
    if semester is None:
        semester = db.scalar(
            select(Semester)
            .order_by(Semester.starts_on.desc(), Semester.id.desc())
            .limit(1)
        )
    return semester.id if semester is not None else None


def _claimable_from_catalog(db: Session, *, teacher_id: int, semester_id: int):
    """认领列表依赖只读目录；本人授课与历史记录仍可独立查询。"""
    offerings = teaching.list_claimable_offerings(
        db, teacher_id=teacher_id, semester_id=semester_id
    )
    semester = db.get(Semester, semester_id)
    snapshot = catalog.fetch_catalog(semester.code)
    if snapshot.semester_code != semester.code:
        raise BusinessError("外部目录返回了其他学期，拒绝使用", 503)
    return offerings


@router.get("/teacher/claimable-sections")
def page_claimable_sections(
    semester_id: int | None = Query(default=None, ge=1),
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """教师教学任务认领页：可认领班次列表。"""
    target_semester_id = semester_id or _default_semester_id(db)
    if target_semester_id is None:
        return {"semester_id": None, "offerings": []}
    offerings = _claimable_from_catalog(
        db, teacher_id=teacher_id, semester_id=target_semester_id
    )
    return {
        "semester_id": target_semester_id,
        "offerings": [
            {
                "id": item["offering_id"],
                "course_code": item["course_code"],
                "course_name": item["course_name"],
                "fee": str(item["fee"]),
                "capacity": item["capacity"],
                "enrolled_count": item["enrolled_count"],
                "section_number": item["section_number"],
                "semester_id": item["semester_id"],
                "schedule_time": _format_schedule(item["slots"]),
                "teacher_id": item["teacher_id"],
                "is_mine": item["is_claimed_by_me"],
                "status": item["status"],
            }
            for item in offerings
        ],
    }


@router.get("/teacher/my-sections")
def page_my_sections(
    semester_id: int | None = Query(default=None, ge=1),
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """教师自己的授课列表，供花名册与成绩页的下拉框使用；可按学期过滤。"""
    offerings = teaching.list_my_offerings(
        db, teacher_id=teacher_id, semester_id=semester_id
    )
    codes = _semester_codes(db)
    return {
        "offerings": [
            {
                "id": item["offering_id"],
                "course_code": item["course_code"],
                "course_name": item["course_name"],
                "section_number": item["section_number"],
                "semester_id": item["semester_id"],
                "semester_code": codes.get(item["semester_id"]),
                "capacity": item["capacity"],
                "enrolled_count": item["enrolled_count"],
                "schedule_time": _format_schedule(item["slots"]),
                "status": item["status"],
                "teacher_id": item["teacher_id"],
                "is_mine": item["is_claimed_by_me"],
            }
            for item in offerings
        ]
    }


@router.post(
    "/teacher/sections/{section_id}/claim",
    dependencies=[Depends(require_csrf_token)],
)
def page_claim_section(
    section_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    offering = teaching.claim_offering(
        db, teacher_id=teacher_id, offering_id=section_id
    )
    return {
        "message": "认领成功",
        "section_id": offering.id,
        "teacher_id": offering.teacher_id,
    }


@router.post(
    "/teacher/sections/{section_id}/unclaim",
    dependencies=[Depends(require_csrf_token)],
)
def page_unclaim_section(
    section_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    offering = teaching.release_offering(
        db, teacher_id=teacher_id, offering_id=section_id
    )
    return {
        "message": "已释放班次认领",
        "section_id": offering.id,
        "teacher_id": offering.teacher_id,
    }


@router.get("/teacher/sections/{section_id}/roster")
def page_offering_roster(
    section_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """花名册页：所教班次的正式学生名单。"""
    roster = teaching.get_offering_roster(
        db, teacher_id=teacher_id, offering_id=section_id
    )
    return {
        "offering_id": roster["offering"]["offering_id"],
        "course_code": roster["offering"]["course_code"],
        "course_name": roster["offering"]["course_name"],
        "students": [
            {
                "student_id": item["student_id"],
                "student_number": item["student_number"],
                "full_name": item["name"],
                "enrolled_at": _format_moment(item["enrolled_at"]),
            }
            for item in roster["students"]
        ],
    }


@router.get("/teacher/sections/{section_id}/grades")
def page_offering_grades(
    section_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """成绩录入页：名单与当前成绩（A-F/I，留空表示未评定）。"""
    sheet = teaching.list_offering_grades(
        db, teacher_id=teacher_id, offering_id=section_id
    )
    return {
        "offering_id": sheet["offering"]["offering_id"],
        "semester_status": sheet["semester_status"],
        "editable": sheet["editable"],
        "students": [
            {
                "student_id": item["student_id"],
                "student_number": item["student_number"],
                "full_name": item["name"],
                "value": item["value"],
                "updated_at": _format_moment(item["updated_at"]),
                "updated_by": item["updated_by"],
            }
            for item in sheet["students"]
        ],
    }


@router.get("/teacher/sections/{section_id}/grade-changes")
def page_offering_grade_changes(
    section_id: int,
    student_id: int | None = Query(default=None, ge=1),
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """成绩录入页的变更记录入口；字段与 REST 路径一致。"""
    return teaching.list_grade_changes(
        db,
        teacher_id=teacher_id,
        offering_id=section_id,
        student_id=student_id,
    )


class PageGradeEntry(BaseModel):
    # 拒绝旧版模拟接口的 {student_id, score} 形式，避免被当成"留空"而静默清空成绩
    model_config = ConfigDict(extra="forbid")

    student_id: int
    # 空字符串与 null 都表示留空成绩；非空时必须是 A-F/I
    value: str | None = None


class PageSaveGradesRequest(BaseModel):
    grades: list[PageGradeEntry] = Field(default_factory=list, max_length=200)


@router.post(
    "/teacher/sections/{section_id}/grades",
    dependencies=[Depends(require_csrf_token)],
)
def page_save_offering_grades(
    section_id: int,
    body: PageSaveGradesRequest,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """成绩录入页提交入口。"""
    summary = teaching.save_offering_grades(
        db,
        teacher_id=teacher_id,
        offering_id=section_id,
        entries=[
            {"student_id": item.student_id, "value": item.value} for item in body.grades
        ],
    )
    return {
        "message": f"成功保存 {summary['updated']} 条学生成绩",
        **summary,
    }


@router.get("/student/grades")
def page_student_grades(
    student_id: int = Depends(require_student), db: Session = Depends(get_db)
):
    """学生成绩单页：本人全部成绩（等级制）。"""
    result = teaching.list_student_grades(db, student_id=student_id)
    items = [
        {
            "semester": item["semester_code"],
            "semester_id": item["semester_id"],
            "course_code": item["course_code"],
            "course_name": item["course_name"],
            "value": item["value"],
            "is_passed": item["passed"],
            "offering_id": item["offering_id"],
            "section_number": item["section_number"],
        }
        for item in result["grades"]
    ]
    return {
        "student_number": result["student_number"],
        "total_count": len(items),
        "passed_count": sum(1 for item in items if item["is_passed"]),
        "items": items,
    }
