"""Complete personnel, billing evidence and concurrent login number allocation.

Revision ID: final20261008
Revises: 686cacbbbda0
"""

from alembic import op
import sqlalchemy as sa

revision = "final20261008"
down_revision = "686cacbbbda0"
branch_labels = None
depends_on = None


def upgrade():
    # 旧演示脚本曾设为 30；若已有超过十人的班次，则拒绝静默缩容。
    connection = op.get_bind()
    overfull = connection.execute(
        sa.text(
            "SELECT offering_id FROM enrollments GROUP BY offering_id HAVING COUNT(*) > 10"
        )
    ).first()
    if overfull:
        raise RuntimeError("已有班次超过十人，请先人工核对选课记录再升级")
    connection.execute(sa.text("UPDATE offerings SET capacity=10 WHERE capacity>10"))
    with op.batch_alter_table("offerings") as batch:
        batch.create_check_constraint("ck_offering_capacity_max", "capacity <= 10")
    for table in ("students", "teachers"):
        op.add_column(table, sa.Column("birth_date", sa.Date(), nullable=True))
        op.add_column(
            table, sa.Column("social_security_number", sa.String(32), nullable=True)
        )
        op.add_column(
            table,
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        )
    op.add_column("students", sa.Column("graduation_date", sa.Date(), nullable=True))
    op.add_column(
        "billing_jobs", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "login_number_sequences",
        sa.Column("prefix", sa.String(20), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False),
    )


def downgrade():
    with op.batch_alter_table("offerings") as batch:
        batch.drop_constraint("ck_offering_capacity_max", type_="check")
    op.drop_table("login_number_sequences")
    op.drop_column("billing_jobs", "sent_at")
    op.drop_column("students", "graduation_date")
    for table in ("teachers", "students"):
        for column in ("status", "social_security_number", "birth_date"):
            op.drop_column(table, column)
