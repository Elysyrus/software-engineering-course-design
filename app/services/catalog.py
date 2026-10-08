"""只读外部目录适配。目录定义课程，注册库保存本地班次名额与选课结果。"""

from datetime import date
from decimal import Decimal

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import BusinessError
from app.models import (
    Course,
    CoursePrerequisite,
    Offering,
    OfferingSlot,
    Semester,
    SemesterStatus,
)


class Slot(BaseModel):
    weekday: int = Field(ge=1, le=7)
    start_period: int = Field(ge=1, le=20)
    end_period: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_period < self.start_period:
            raise ValueError("节次范围无效")
        return self


class Section(BaseModel):
    section_number: str = Field(min_length=1, max_length=20)
    capacity: int = Field(default=10, ge=1, le=10)
    slots: list[Slot] = Field(min_length=1)


class CatalogCourse(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    department: str = ""
    fee: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    prerequisites: list[str] = Field(default_factory=list)
    sections: list[Section]


class CatalogSnapshot(BaseModel):
    semester_code: str = Field(min_length=1, max_length=32)
    starts_on: date
    ends_on: date
    courses: list[CatalogCourse]

    @model_validator(mode="after")
    def consistent(self):
        codes = [item.code for item in self.courses]
        if self.ends_on < self.starts_on or len(codes) != len(set(codes)):
            raise ValueError("目录日期或课程编号无效")
        for course in self.courses:
            sections = [s.section_number for s in course.sections]
            if (
                len(sections) != len(set(sections))
                or course.code in course.prerequisites
                or not set(course.prerequisites) <= set(codes)
            ):
                raise ValueError("目录班号或先修信息无效")
        return self


def fetch_catalog(semester_code: str | None = None) -> CatalogSnapshot:
    settings = get_settings()
    try:
        response = httpx.get(
            settings.catalog_service_url.rstrip("/") + "/catalog/courses",
            params={"semester_code": semester_code} if semester_code else {},
            timeout=settings.catalog_request_timeout_seconds,
        )
        response.raise_for_status()
        return CatalogSnapshot.model_validate(response.json()["data"])
    except (httpx.HTTPError, ValidationError, KeyError, TypeError, ValueError) as error:
        raise BusinessError(
            "课程目录暂时不可用或数据无效，请稍后重试；已保存的选课结果不会丢失", 503
        ) from error


def import_catalog(db: Session, snapshot: CatalogSnapshot) -> Semester:
    """显式初始化/同步本地镜像，不向旧目录发送写请求。已存在班次不覆盖其状态和教师。"""
    semester = db.scalar(
        select(Semester)
        .where(Semester.code == snapshot.semester_code)
        .with_for_update()
    )
    if semester and semester.status != SemesterStatus.OPEN:
        raise BusinessError("已关闭学期不能重新同步目录")
    if semester is None:
        semester = Semester(
            code=snapshot.semester_code,
            starts_on=snapshot.starts_on,
            ends_on=snapshot.ends_on,
        )
        db.add(semester)
        db.flush()
    courses = {}
    for item in snapshot.courses:
        course = db.scalar(select(Course).where(Course.code == item.code))
        if course is None:
            course = Course(
                code=item.code,
                name=item.name,
                description=item.description,
                fee=item.fee,
            )
            db.add(course)
            db.flush()
        # 业务已引用的课程保持历史定义；元数据变化应由教务评审后专门迁移。
        courses[item.code] = course
        for section in item.sections:
            existing = db.scalar(
                select(Offering).where(
                    Offering.semester_id == semester.id,
                    Offering.course_id == course.id,
                    Offering.section_number == section.section_number,
                )
            )
            if existing is None:
                existing = Offering(
                    semester_id=semester.id,
                    course_id=course.id,
                    section_number=section.section_number,
                    capacity=section.capacity,
                )
                existing.slots = [
                    OfferingSlot(**slot.model_dump()) for slot in section.slots
                ]
                db.add(existing)
    db.flush()
    for item in snapshot.courses:
        for prerequisite in item.prerequisites:
            target = courses[prerequisite].id
            if (
                db.scalar(
                    select(CoursePrerequisite).where(
                        CoursePrerequisite.course_id == courses[item.code].id,
                        CoursePrerequisite.prerequisite_course_id == target,
                    )
                )
                is None
            ):
                db.add(
                    CoursePrerequisite(
                        course_id=courses[item.code].id, prerequisite_course_id=target
                    )
                )
    db.commit()
    return semester
