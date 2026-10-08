"""注册关闭与学期结束是两件事。当前以关闭注册且结束日期已过表示已完成。"""

from datetime import UTC, date, datetime, timedelta

from app.models import Semester, SemesterStatus


def today() -> date:
    return (datetime.now(UTC) + timedelta(hours=8)).date()


def is_completed(semester: Semester) -> bool:
    return semester.status == SemesterStatus.CLOSED and semester.ends_on < today()
