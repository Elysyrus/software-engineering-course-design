"""数据库迁移的端到端冒烟测试：在临时 SQLite 库上真实执行 Alembic。"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMBER4_REVISION_PARENT = "da4f6a565975"


def _run_alembic(arguments: list[str], database_url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )


def _table_names(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row[0] for row in rows}


def test_migrations_add_and_remove_member4_tables(tmp_path: Path):
    database_path = tmp_path / "migration_check.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    upgrade = _run_alembic(["upgrade", "head"], database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    tables = _table_names(database_path)
    assert {"teacher_qualifications", "grade_changes"} <= tables
    assert {"grades", "offerings", "enrollments"} <= tables

    downgrade = _run_alembic(["downgrade", MEMBER4_REVISION_PARENT], database_url)
    assert downgrade.returncode == 0, downgrade.stderr

    tables = _table_names(database_path)
    assert "teacher_qualifications" not in tables
    assert "grade_changes" not in tables
