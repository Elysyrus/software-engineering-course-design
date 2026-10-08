"""add account and server session tables

Revision ID: da4f6a565975
Revises: 51ba2d09cfed
Create Date: 2026-09-13 13:56:21.292419

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da4f6a565975'
down_revision: Union[str, Sequence[str], None] = '51ba2d09cfed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite 不支持 ALTER COLUMN；batch 模式会安全地重建 accounts 表并保留数据。
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.alter_column(
            "subject_id",
            existing_type=sa.INTEGER(),
            nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.alter_column(
            "subject_id",
            existing_type=sa.INTEGER(),
            nullable=False,
        )
