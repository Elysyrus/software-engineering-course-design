from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SemesterStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class OfferingStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ChoiceKind(str, enum.Enum):
    PRIMARY = "primary"
    ALTERNATE = "alternate"


class BillingStatus(str, enum.Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class GradeValue(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"
    I = "I"


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_number: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_number: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Semester(Base):
    __tablename__ = "semesters"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    status: Mapped[SemesterStatus] = mapped_column(Enum(SemesterStatus), default=SemesterStatus.OPEN)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class CoursePrerequisite(Base):
    __tablename__ = "course_prerequisites"
    __table_args__ = (UniqueConstraint("course_id", "prerequisite_course_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    prerequisite_course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="RESTRICT"))


class Offering(Base):
    __tablename__ = "offerings"
    __table_args__ = (
        UniqueConstraint("semester_id", "course_id", "section_number"),
        CheckConstraint("capacity >= 1", name="ck_offering_capacity_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id", ondelete="RESTRICT"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="RESTRICT"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True)
    section_number: Mapped[str] = mapped_column(String(20))
    capacity: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[OfferingStatus] = mapped_column(Enum(OfferingStatus), default=OfferingStatus.OPEN)

    course: Mapped[Course] = relationship()
    slots: Mapped[list[OfferingSlot]] = relationship(cascade="all, delete-orphan")


class OfferingSlot(Base):
    __tablename__ = "offering_slots"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_slot_weekday"),
        CheckConstraint("start_period >= 1 AND end_period >= start_period", name="ck_slot_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_period: Mapped[int] = mapped_column(Integer)
    end_period: Mapped[int] = mapped_column(Integer)


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("student_id", "semester_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id", ondelete="RESTRICT"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    has_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    last_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DraftChoice(Base):
    __tablename__ = "draft_choices"
    __table_args__ = (
        UniqueConstraint("schedule_id", "offering_id"),
        UniqueConstraint("schedule_id", "alternate_priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), index=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="RESTRICT"))
    kind: Mapped[ChoiceKind] = mapped_column(Enum(ChoiceKind))
    alternate_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("schedule_id", "offering_id"),
        UniqueConstraint("schedule_id", "course_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), index=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="RESTRICT"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="RESTRICT"))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class FormalAlternate(Base):
    __tablename__ = "formal_alternates"
    __table_args__ = (
        UniqueConstraint("schedule_id", "offering_id"),
        UniqueConstraint("schedule_id", "priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), index=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="RESTRICT"))
    priority: Mapped[int] = mapped_column(Integer)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Grade(Base):
    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("student_id", "offering_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="RESTRICT"))
    value: Mapped[GradeValue | None] = mapped_column(Enum(GradeValue), nullable=True)


class BillingJob(Base):
    __tablename__ = "billing_jobs"
    __table_args__ = (UniqueConstraint("semester_id", "student_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id", ondelete="RESTRICT"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    schedule_snapshot: Mapped[str] = mapped_column(Text)
    status: Mapped[BillingStatus] = mapped_column(Enum(BillingStatus), default=BillingStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
