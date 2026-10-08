"""计费接收账本落到独立 SQLite 库，进程重启后仍保持去重结果。"""

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


class PersistentBillingStore:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS charges (id INTEGER PRIMARY KEY, semester_id INTEGER NOT NULL, student_id INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(semester_id,student_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS conflicts (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def receive(self, request):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,payload FROM charges WHERE semester_id=? AND student_id=?",
                (request.semester_id, request.student_id),
            ).fetchone()
            moment = datetime.now(UTC).isoformat()
            if row is None:
                payload = dict(
                    semester_id=request.semester_id,
                    student_id=request.student_id,
                    semester_code=request.semester_code,
                    student_number=request.student_number,
                    amount=str(request.amount),
                    schedule_snapshot=request.schedule_snapshot,
                    attempts=1,
                    received_at=moment,
                )
                cursor = db.execute(
                    "INSERT INTO charges (semester_id,student_id,payload) VALUES (?,?,?)",
                    (request.semester_id, request.student_id, json.dumps(payload)),
                )
                payload["charge_id"] = cursor.lastrowid
                db.execute(
                    "UPDATE charges SET payload=? WHERE id=?",
                    (json.dumps(payload), cursor.lastrowid),
                )
                return "created", payload
            payload = json.loads(row[1])
            if (
                Decimal(payload["amount"]) == request.amount
                and payload["schedule_snapshot"] == request.schedule_snapshot
            ):
                payload["attempts"] += 1
                payload["received_at"] = moment
                db.execute(
                    "UPDATE charges SET payload=? WHERE id=?",
                    (json.dumps(payload), row[0]),
                )
                return "duplicate", payload
            conflict = dict(
                semester_id=request.semester_id,
                student_id=request.student_id,
                existing_amount=payload["amount"],
                received_amount=str(request.amount),
                content_matches=False,
                reason="同一学期与学生的账单内容不一致",
                received_at=moment,
            )
            db.execute(
                "INSERT INTO conflicts(payload) VALUES (?)", (json.dumps(conflict),)
            )
            return "conflict", conflict

    def charges(self):
        with self._connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM charges ORDER BY id")
            ]

    def charge_of(self, semester_id, student_id):
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM charges WHERE semester_id=? AND student_id=?",
                (semester_id, student_id),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def conflicts(self):
        with self._connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM conflicts ORDER BY id")
            ]

    def reset(self):
        with self._connect() as db:
            db.execute("DELETE FROM charges")
            db.execute("DELETE FROM conflicts")
