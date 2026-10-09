"""MySQL 时间列保留微秒，避免 DATETIME 四舍五入把时间进位到未来。

Revision ID: fsp20261009
Revises: final20261008
"""

from alembic import op
from sqlalchemy.dialects import mysql

revision = "fsp20261009"
down_revision = "final20261008"
branch_labels = None
depends_on = None

# (表, 列, 是否允许为空)；与 app/models.py 中使用 Timestamp 的列一一对应。
COLUMNS = (
    ("accounts", "created_at", False),
    ("accounts", "updated_at", False),
    ("semesters", "closed_at", True),
    ("billing_jobs", "next_attempt_at", False),
    ("billing_jobs", "sent_at", True),
    ("billing_jobs", "created_at", False),
    ("schedules", "last_submitted_at", True),
    ("server_sessions", "created_at", False),
    ("server_sessions", "expires_at", False),
    ("server_sessions", "revoked_at", True),
    ("enrollments", "enrolled_at", False),
    ("formal_alternates", "consumed_at", True),
    ("grade_changes", "changed_at", False),
)


def _alter(fsp: int) -> None:
    # SQLite 本身按文本保存完整时间，不需要调整。
    if op.get_bind().dialect.name != "mysql":
        return
    for table, column, nullable in COLUMNS:
        op.alter_column(
            table,
            column,
            type_=mysql.DATETIME(fsp=fsp),
            existing_nullable=nullable,
            nullable=nullable,
        )


def upgrade():
    _alter(6)


def downgrade():
    _alter(0)
