"""计费模拟接收服务：外部计费系统的替身，供账单发送与重试联调、验收。

运行方式：

    ./.venv/Scripts/python.exe mock_billing/mock_billing_service.py --port 8082

约定（与 docs/接口约定v0.2.md 一致）：

- 账单唯一键是 ``(semester_id, student_id)``；
- 相同键、相同内容重复接收返回既有成功结果，不重复收费；
- 相同键、不同内容返回 ``409``，并保留失败记录供排查；
- ``fault=503 / 500 / timeout`` 用于验证发送端的失败与重试。

本服务是外部系统，不导入 ``app`` 下的任何模型或业务逻辑。
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field


class ChargeRequest(BaseModel):
    semester_id: int
    student_id: int
    amount: Decimal = Field(ge=0)
    schedule_snapshot: str
    semester_code: str | None = None
    student_number: str | None = None


class BillingStore:
    """内存账单账本：按 (学期, 学生) 去重，并记录被拒的重复冲突。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._charges: dict[tuple[int, int], dict] = {}
        self._conflicts: list[dict] = []

    def reset(self) -> None:
        with self._lock:
            self._charges.clear()
            self._conflicts.clear()

    def receive(self, request: ChargeRequest) -> tuple[str, dict]:
        key = (request.semester_id, request.student_id)
        with self._lock:
            existing = self._charges.get(key)
            if existing is None:
                charge = {
                    "charge_id": len(self._charges) + 1,
                    "semester_id": request.semester_id,
                    "semester_code": request.semester_code,
                    "student_id": request.student_id,
                    "student_number": request.student_number,
                    "amount": str(request.amount),
                    "schedule_snapshot": request.schedule_snapshot,
                    "attempts": 1,
                    "received_at": datetime.now(UTC).isoformat(),
                }
                self._charges[key] = charge
                return "created", charge

            # 金额按数值比较，避免 "120.0" 与 "120.00" 被误判为内容冲突
            same_content = (
                Decimal(existing["amount"]) == request.amount
                and existing["schedule_snapshot"] == request.schedule_snapshot
            )
            if same_content:
                existing["attempts"] += 1
                existing["received_at"] = datetime.now(UTC).isoformat()
                return "duplicate", existing

            conflict = {
                "semester_id": request.semester_id,
                "student_id": request.student_id,
                "existing_amount": existing["amount"],
                "received_amount": str(request.amount),
                "content_matches": False,
                "reason": "同一学期与学生的账单内容不一致",
                "received_at": datetime.now(UTC).isoformat(),
            }
            self._conflicts.append(conflict)
            return "conflict", conflict

    def charges(self) -> list[dict]:
        with self._lock:
            return list(self._charges.values())

    def charge_of(self, semester_id: int, student_id: int) -> dict | None:
        with self._lock:
            return self._charges.get((semester_id, student_id))

    def conflicts(self) -> list[dict]:
        with self._lock:
            return list(self._conflicts)


app = FastAPI(title="SECD 计费模拟接收服务")
from mock_billing.persistent_store import PersistentBillingStore

store = (
    PersistentBillingStore(os.environ["MOCK_BILLING_DATABASE"])
    if os.getenv("MOCK_BILLING_DATABASE")
    else BillingStore()
)


def _inject_fault(fault: str, timeout_seconds: float) -> None:
    if fault == "503":
        raise HTTPException(status_code=503, detail="计费服务暂时不可用 (Mock 503)")
    if fault == "500":
        raise HTTPException(status_code=500, detail="计费服务内部错误 (Mock 500)")
    if fault == "timeout":
        time.sleep(timeout_seconds)


@app.post("/billing/charges", status_code=201)
def receive_charge(
    request: ChargeRequest,
    response: Response,
    fault: str = Query(
        default="none", description="故障注入: none | 503 | 500 | timeout"
    ),
    timeout_seconds: float = Query(default=5.0, ge=0, le=60),
):
    """接收账单；重复发送同一账单返回既有结果，不重复收费。"""
    _inject_fault(fault, timeout_seconds)
    outcome, payload = store.receive(request)
    if outcome == "created":
        return {"status": "accepted", "duplicate": False, "charge": payload}
    if outcome == "duplicate":
        # 路由默认 201 只适用于首次收费；重复接收必须返回 200 表示复用既有结果
        response.status_code = 200
        return {"status": "already_charged", "duplicate": True, "charge": payload}
    raise HTTPException(
        status_code=409,
        detail={
            "message": "同一学期与学生的账单内容不一致",
            "existing_amount": payload["existing_amount"],
            "received_amount": payload["received_amount"],
        },
    )


@app.get("/billing/charges")
def list_charges():
    charges = store.charges()
    return {"count": len(charges), "charges": charges}


@app.get("/billing/charges/{semester_id}/{student_id}")
def get_charge(semester_id: int, student_id: int):
    charge = store.charge_of(semester_id, student_id)
    if charge is None:
        raise HTTPException(status_code=404, detail="账单不存在")
    return charge


@app.get("/billing/conflicts")
def list_conflicts():
    conflicts = store.conflicts()
    return {"count": len(conflicts), "conflicts": conflicts}


@app.post("/billing/reset")
def reset():
    """清空账本，便于重复演示与自动化测试。"""
    store.reset()
    return {"status": "reset"}


def main() -> None:
    parser = argparse.ArgumentParser(description="启动计费模拟接收服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8082)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
