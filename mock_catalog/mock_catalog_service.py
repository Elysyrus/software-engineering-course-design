"""独立只读目录；注册应用仅执行 GET，不向外部目录写入。"""

import asyncio
import json
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="只读课程目录模拟服务")


def load_catalog():
    path = Path(
        os.getenv(
            "MOCK_CATALOG_FILE", str(Path(__file__).with_name("catalog_data.json"))
        )
    )
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/catalog/courses")
async def courses(semester_code: str | None = None, fault: str = Query("none")):
    if fault == "503" or os.getenv("MOCK_CATALOG_FAULT") == "503":
        raise HTTPException(503, "目录服务暂时不可用")
    if fault == "timeout":
        await asyncio.sleep(5)
    data = load_catalog()
    if semester_code and semester_code != data["semester_code"]:
        raise HTTPException(404, "目录中没有此学期")
    if fault == "empty":
        data["courses"] = []
    return {"data": data}
