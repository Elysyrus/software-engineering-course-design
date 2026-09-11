"""
Course Catalog Mock Subsystem Service
按只读接口约定提供课程和班次查询，并支持模拟故障场景。
运行方式：
    python mock_catalog/mock_catalog_service.py --port 8081
"""

from fastapi import FastAPI, HTTPException, Query
import uvicorn
import time

app = FastAPI(title="SECD Read-only Course Catalog Mock Subsystem")

# 模拟基础课程与班次数据
MOCK_COURSES = [
    {
        "id": 1,
        "course_code": "CS101",
        "course_name": "面向对象软件工程",
        "credits": 4.0,
        "department": "计算机科学与技术学院",
        "sections": [
            {
                "section_id": 1011,
                "teacher_name": "张教授",
                "capacity": 30,
                "enrolled_count": 18,
                "schedule_time": "周一 08:00-09:40",
                "classroom": "正新楼 301"
            },
            {
                "section_id": 1012,
                "teacher_name": "李副教授",
                "capacity": 30,
                "enrolled_count": 30,  # 满额
                "schedule_time": "周二 10:00-11:40",
                "classroom": "正新楼 303"
            }
        ]
    },
    {
        "id": 2,
        "course_code": "CS202",
        "course_name": "操作系统原理",
        "credits": 3.5,
        "department": "软件工程系",
        "sections": [
            {
                "section_id": 2021,
                "teacher_name": "王高级工程师",
                "capacity": 25,
                "enrolled_count": 12,
                "schedule_time": "周三 13:30-15:10",
                "classroom": "王湘浩楼 B108"
            }
        ]
    },
    {
        "id": 3,
        "course_code": "CS303",
        "course_name": "分布式数据库技术",
        "credits": 3.0,
        "department": "软件工程系",
        "sections": [
            {
                "section_id": 3031,
                "teacher_name": "赵教授",
                "capacity": 40,
                "enrolled_count": 39,
                "schedule_time": "周四 15:30-17:10",
                "classroom": "正新楼 102"
            }
        ]
    }
]

@app.get("/catalog/courses")
async def get_courses(
    fault: str = Query(default="none", description="故障注入类型: none | 503 | timeout | empty")
):
    """
    提供课程目录列表。
    可注入故障场景：
    - fault=503: 模拟目录服务临时宕机/过载
    - fault=timeout: 模拟网络严重卡顿响应超时 (延迟 5 秒)
    - fault=empty: 模拟无开放课程或空数据场景
    """
    if fault == "503":
        raise HTTPException(status_code=503, detail="外部只读课程目录服务临时过载或不可用 (Mock 503)")

    if fault == "timeout":
        time.sleep(5.0)

    if fault == "empty":
        return {"code": 200, "message": "success", "data": []}

    return {
        "code": 200,
        "message": "success",
        "timestamp": int(time.time()),
        "data": MOCK_COURSES
    }

@app.get("/catalog/sections/{section_id}")
async def get_section_detail(section_id: int):
    """查询指定班次详情"""
    for course in MOCK_COURSES:
        for sec in course["sections"]:
            if sec["section_id"] == section_id:
                return {
                    "code": 200,
                    "data": {
                        **sec,
                        "course_code": course["course_code"],
                        "course_name": course["course_name"],
                        "credits": course["credits"]
                    }
                }
    raise HTTPException(status_code=404, detail="班次不存在")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)