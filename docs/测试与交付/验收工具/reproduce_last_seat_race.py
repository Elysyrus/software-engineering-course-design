"""最后一个名额竞争复测：两名学生同时抢最后一个名额，检查是否超额。

每轮清空重建 MySQL 验收库（库名必须以 _test 结尾），启动主应用与目录服务，让 9 人先占满 ACC101 的 9 个名额，
再让 2 名学生同时提交含 ACC101 的方案，记录成功人数与 ACC101 最终人数。

运行（项目根目录）：
    export ACCEPTANCE_DATABASE_URL='mysql+pymysql://用户:密码@127.0.0.1:3306/course_acceptance_test?charset=utf8mb4'
    python "docs/测试与交付/验收工具/reproduce_last_seat_race.py" --rounds 30
输出：docs/测试与交付/验收输出/last_seat_race.json
"""

import argparse
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_acceptance as ra  # noqa: E402


def one_round(args, index: int) -> dict:
    ns = argparse.Namespace(
        port=args.port + index * 3,
        catalog_port=args.port + index * 3 + 1,
        billing_port=args.port + index * 3 + 2,
        keep=False,
        database_url=args.database_url,
    )
    env = ra.Environment(ns)
    try:
        env.prepare()
        users = {}
        for i in range(1, 12):
            label = f"S{i:03}" if i <= 10 else None
            if label is None:
                created = ra.User(env, "registrar")
                created.activate()
                number = created.write(
                    "POST", "/api/v1/admin/users", json={"full_name": "竞争学生", "role": "student"}
                ).json()["login_number"]
                label = number
            users[label] = ra.User(env, label)
            users[label].activate()
        teacher = ra.User(env, "T001")
        teacher.activate()
        any_user = users["S001"]
        sections = any_user.get("/api/v1/student/available-sections").json()
        semester_id = sections["semester_id"]
        ids = {row["course_code"]: row["id"] for row in sections["sections"] if row["section_number"] == "01"}
        teacher.write("POST", f"/api/v1/teacher/offerings/{ids['ACC101']}/claim")
        labels = list(users)
        plan = (["ACC101", "ACC102", "ACC103", "ACC104"], ["ACC109", "ACC110"])
        for label in labels[:9]:
            ra.save_and_submit(users[label], semester_id, ids, *plan)
        racers = labels[9:11]
        for label in racers:
            ra.save_draft(users[label], semester_id, ids, *plan)
        versions = {label: ra.schedule(users[label], semester_id)["version"] for label in racers}
        barrier = threading.Barrier(2)
        outcomes = {}

        def race(label):
            barrier.wait()
            outcomes[label] = users[label].write(
                "POST", f"/semesters/{semester_id}/schedule/submit",
                json={"expected_version": versions[label]},
            ).status_code

        threads = [threading.Thread(target=race, args=(label,)) for label in racers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        count = ra.seat_counts(any_user, semester_id)["ACC101-01"]
        return {"round": index + 1, "status_codes": list(outcomes.values()), "acc101_count": count,
                "overbooked": count > 10}
    finally:
        env.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--port", type=int, default=9300)
    parser.add_argument("--database-url", default=ra.os.getenv("ACCEPTANCE_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url or not args.database_url.startswith("mysql"):
        parser.error("请用 --database-url 或 ACCEPTANCE_DATABASE_URL 指定 MySQL 验收库")
    rounds = []
    for index in range(args.rounds):
        ra.log_lines.clear()
        result = one_round(args, index)
        rounds.append(result)
        print(result, flush=True)
    overbooked = sum(1 for item in rounds if item["overbooked"])
    summary = {
        "executed_at": datetime.now().isoformat(timespec="seconds"),
        "database": ra._database_label(args.database_url),
        "rounds": len(rounds),
        "overbooked_rounds": overbooked,
        "details": rounds,
    }
    ra.OUTPUT.mkdir(parents=True, exist_ok=True)
    (ra.OUTPUT / "last_seat_race.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"共 {len(rounds)} 轮，超额 {overbooked} 轮")


if __name__ == "__main__":
    main()
