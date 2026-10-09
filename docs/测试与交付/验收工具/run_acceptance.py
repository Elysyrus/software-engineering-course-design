"""成员 6 接口级验收执行脚本：在全新临时环境中启动真实系统，逐条执行验收用例并记录实际响应。

与 tests/ 下开发者的自动化测试不同，本脚本不导入业务函数，只通过 HTTP 调用
正在运行的主应用、只读目录和计费接收端，并在用例中间真实停止/恢复外部服务。

数据库使用 MySQL 8 专用验收库（库名必须以 _test 结尾，每次执行前清空重建）。

运行（项目根目录，需已安装 requirements.txt）：
    export ACCEPTANCE_DATABASE_URL='mysql+pymysql://用户:密码@127.0.0.1:3306/course_acceptance_test?charset=utf8mb4'
    python "docs/测试与交付/验收工具/run_acceptance.py"
也可用 --database-url 传入。可选参数：--port 8950 --catalog-port 8951 --billing-port 8952 --keep
输出：docs/测试与交付/验收输出/acceptance_results.json 与 acceptance_log.txt

用例编号与《测试用例》文档一一对应。脚本只把实际响应满足预期的用例记为通过。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUTPUT = HERE.parent / "验收输出"
INITIAL = "Initial123"
NEW_PASSWORD = "Accept2027a"

results: list[dict] = []
log_lines: list[str] = []


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    log_lines.append(line)


def summarize(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = response.text[:200]
    text = json.dumps(body, ensure_ascii=False)
    return f"HTTP {response.status_code} {text[:300]}"


def record(case_id: str, title: str, expected: str, actual: str, passed: bool) -> None:
    results.append(
        {
            "id": case_id,
            "title": title,
            "expected": expected,
            "actual": actual,
            "result": "通过" if passed else "不通过",
        }
    )
    log(f"{case_id} {'通过' if passed else '不通过'} | {title} | 实际：{actual}")


# --------------------------------------------------------------------------
# 环境
# --------------------------------------------------------------------------


class Environment:
    def __init__(self, args) -> None:
        self.args = args
        self.workdir = Path(tempfile.mkdtemp(prefix="m6-acceptance-"))
        self.base = f"http://127.0.0.1:{args.port}"
        self.catalog_url = f"http://127.0.0.1:{args.catalog_port}"
        self.billing_url = f"http://127.0.0.1:{args.billing_port}"
        self.database_url = args.database_url
        self.env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "APP_ENV": "development",
            "DATABASE_URL": self.database_url,
            "CATALOG_SERVICE_URL": self.catalog_url,
            "BILLING_SERVICE_URL": self.billing_url,
            # 缩短重试间隔，便于在一次执行中观察“失败 -> 恢复 -> 自动重发”
            "BILLING_RETRY_SECONDS": "1",
            "MOCK_CATALOG_FILE": str(HERE / "acceptance_catalog.json"),
            "MOCK_BILLING_DATABASE": str(self.workdir / "mock_billing.db"),
        }
        self.processes: dict[str, subprocess.Popen] = {}

    def run(self, *command: str) -> None:
        completed = subprocess.run(
            [sys.executable, *command],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
        )
        log(f"执行 {' '.join(command)} -> 退出码 {completed.returncode}")
        if completed.returncode != 0:
            raise SystemExit(completed.stdout + completed.stderr)

    def start(self, name: str) -> None:
        apps = {
            "catalog": ("mock_catalog.mock_catalog_service:app", self.args.catalog_port, "/health"),
            "billing": ("mock_billing.mock_billing_service:app", self.args.billing_port, "/billing/charges"),
            "app": ("app.main:app", self.args.port, "/health"),
        }
        target, port, probe = apps[name]
        logfile = (self.workdir / f"{name}.log").open("a", encoding="utf-8")
        self.processes[name] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=self.env,
            stdout=logfile,
            stderr=subprocess.STDOUT,
        )
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{port}{probe}", timeout=1).status_code == 200:
                    log(f"服务 {name} 已启动（端口 {port}）")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise SystemExit(f"{name} 启动失败，见 {self.workdir / (name + '.log')}")

    def stop(self, name: str) -> None:
        process = self.processes.pop(name, None)
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        log(f"服务 {name} 已停止")

    def reset_mysql(self) -> None:
        """MySQL 验收库每次清空重建；只允许名称以 _test 结尾的专用库。"""
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        url = make_url(self.database_url)
        name = url.database or ""
        if not name.endswith("_test"):
            raise SystemExit("MySQL 验收必须使用名称以 _test 结尾的专用库")
        engine = create_engine(url.set(database=""))
        with engine.begin() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
            conn.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
        engine.dispose()
        log(f"已清空重建 MySQL 验收库 {name}")

    def prepare(self) -> None:
        log(f"临时目录：{self.workdir}")
        self.reset_mysql()
        self.run("-m", "alembic", "upgrade", "head")
        self.run("-m", "scripts.seed_final_demo")
        self.start("catalog")
        self.run("-m", "scripts.sync_catalog")
        self.run(str(HERE / "setup_acceptance_data.py"))
        self.start("billing")
        self.start("app")

    def shutdown(self) -> None:
        for name in list(self.processes):
            self.stop(name)
        if not self.args.keep:
            shutil.rmtree(self.workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# 角色客户端
# --------------------------------------------------------------------------


class User:
    def __init__(self, env: Environment, username: str) -> None:
        self.env = env
        self.username = username
        self.client = httpx.Client(base_url=env.base, timeout=15)

    def headers(self, csrf: bool = True) -> dict:
        token = self.client.cookies.get("csrf_token")
        return {"X-CSRF-Token": token} if csrf and token else {}

    def login(self, password: str) -> httpx.Response:
        self.client.cookies.clear()
        return self.client.post(
            "/api/v1/auth/login", json={"username": self.username, "password": password}
        )

    def change_password(self, old: str, new: str) -> httpx.Response:
        return self.client.post(
            "/api/v1/auth/change-password",
            json={"old_password": old, "new_password": new},
            headers=self.headers(),
        )

    def activate(self) -> None:
        """首次登录 -> 改密 -> 重新登录。"""
        first = self.login(INITIAL)
        if first.status_code != 200:
            raise SystemExit(f"{self.username} 首次登录失败：{summarize(first)}")
        if first.json()["is_first_login"]:
            changed = self.change_password(INITIAL, NEW_PASSWORD)
            if changed.status_code != 200:
                raise SystemExit(f"{self.username} 改密失败：{summarize(changed)}")
        again = self.login(NEW_PASSWORD)
        if again.status_code != 200:
            raise SystemExit(f"{self.username} 改密后登录失败：{summarize(again)}")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.client.get(url, **kwargs)

    def write(self, method: str, url: str, csrf: bool = True, **kwargs) -> httpx.Response:
        return self.client.request(method, url, headers=self.headers(csrf), **kwargs)


# --------------------------------------------------------------------------
# 选课辅助
# --------------------------------------------------------------------------


def schedule(user: User, semester_id: int) -> dict:
    response = user.get(f"/semesters/{semester_id}/schedule")
    response.raise_for_status()
    return response.json()


def enrolled_codes(user: User, semester_id: int, code_of: dict[int, str]) -> list[str]:
    return sorted(code_of[row["offering_id"]] for row in schedule(user, semester_id)["enrolled"])


def save_draft(user: User, semester_id: int, ids: dict, primaries, alternates) -> httpx.Response:
    version = schedule(user, semester_id)["version"]
    choices = [{"offering_id": ids[code], "kind": "primary"} for code in primaries]
    choices += [
        {"offering_id": ids[code], "kind": "alternate", "alternate_priority": index}
        for index, code in enumerate(alternates, start=1)
    ]
    return user.write(
        "PUT",
        f"/semesters/{semester_id}/schedule/draft",
        json={"expected_version": version, "choices": choices},
    )


def submit(user: User, semester_id: int) -> httpx.Response:
    version = schedule(user, semester_id)["version"]
    return user.write(
        "POST", f"/semesters/{semester_id}/schedule/submit", json={"expected_version": version}
    )


def save_and_submit(user, semester_id, ids, primaries, alternates) -> httpx.Response:
    saved = save_draft(user, semester_id, ids, primaries, alternates)
    if saved.status_code != 200:
        return saved
    return submit(user, semester_id)


def seat_counts(user: User, semester_id: int) -> dict[str, int]:
    response = user.get("/api/v1/student/available-sections", params={"semester_id": semester_id})
    response.raise_for_status()
    return {
        f"{row['course_code']}-{row['section_number']}": row["enrolled_count"]
        for row in response.json()["sections"]
    }


# --------------------------------------------------------------------------
# 用例
# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8950)
    parser.add_argument("--catalog-port", type=int, default=8951)
    parser.add_argument("--billing-port", type=int, default=8952)
    parser.add_argument("--keep", action="store_true", help="保留临时数据库与日志")
    parser.add_argument(
        "--database-url",
        default=os.getenv("ACCEPTANCE_DATABASE_URL"),
        help="MySQL 验收库地址，库名必须以 _test 结尾，执行前会清空重建；默认读取 ACCEPTANCE_DATABASE_URL",
    )
    args = parser.parse_args()
    if not args.database_url or not args.database_url.startswith("mysql"):
        parser.error("请用 --database-url 或 ACCEPTANCE_DATABASE_URL 指定 MySQL 验收库")

    env = Environment(args)
    try:
        env.prepare()
        run_cases(env)
    finally:
        env.shutdown()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        passed = sum(1 for item in results if item["result"] == "通过")
        summary = {
            "executed_at": datetime.now().isoformat(timespec="seconds"),
            "database": _database_label(env.database_url),
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "cases": results,
        }
        log(f"共 {len(results)} 条，通过 {passed}，不通过 {len(results) - passed}")
        (OUTPUT / "acceptance_results.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (OUTPUT / "acceptance_log.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")


def _database_label(url: str) -> str:
    from sqlalchemy.engine import make_url

    return f"MySQL（{make_url(url).database}，每次清空重建）"


def run_cases(env: Environment) -> None:
    # ---------------- 登录与身份 ----------------
    anonymous = httpx.Client(base_url=env.base, timeout=10)
    response = anonymous.get("/semesters/1/schedule")
    record("TC-AUTH-01", "未登录访问业务接口", "401", summarize(response), response.status_code == 401)

    probe = User(env, "S001")
    response = probe.login("WrongPass1")
    record("TC-AUTH-02", "错误密码登录", "401，不设置会话", summarize(response),
           response.status_code == 401 and "session_id" not in probe.client.cookies)

    response = probe.login(INITIAL)
    first_login = response.status_code == 200 and response.json().get("is_first_login") is True
    blocked = probe.get("/semesters/1/schedule")
    record("TC-AUTH-03", "首次登录未改密访问业务", "登录返回 is_first_login=true；业务接口 403",
           f"登录 {summarize(response)}；业务 {summarize(blocked)}",
           first_login and blocked.status_code == 403)

    weak = probe.change_password(INITIAL, "abcdefgh")
    record("TC-AUTH-04", "新密码不含数字", "422，提示密码规则", summarize(weak), weak.status_code == 422)

    nocsrf = probe.write("POST", "/api/v1/auth/change-password", csrf=False,
                         json={"old_password": INITIAL, "new_password": NEW_PASSWORD})
    record("TC-AUTH-05", "改密请求缺少 CSRF 令牌", "403", summarize(nocsrf), nocsrf.status_code == 403)

    old_cookie = probe.client.cookies.get("session_id")
    changed = probe.change_password(INITIAL, NEW_PASSWORD)
    # 用改密前的会话 Cookie 单独发请求，验证服务端确实撤销了旧会话（而不只是浏览器删除了 Cookie）
    old_session = httpx.get(f"{env.base}/semesters/1/schedule", cookies={"session_id": old_cookie})
    relogin = probe.login(NEW_PASSWORD)
    record("TC-AUTH-06", "改密成功后旧会话失效、新密码可登录",
           "改密 200；旧会话 401；新密码登录 200 且 is_first_login=false",
           f"改密 {summarize(changed)}；旧会话 {old_session.status_code}；新登录 {summarize(relogin)}",
           changed.status_code == 200 and old_session.status_code == 401
           and relogin.status_code == 200 and relogin.json()["is_first_login"] is False)

    registrar = User(env, "registrar")
    registrar.activate()
    students = {"S001": probe}
    for i in range(2, 11):
        students[f"S{i:03}"] = User(env, f"S{i:03}")
        students[f"S{i:03}"].activate()
    teachers = {}
    for i in range(1, 11):
        teachers[f"T{i:03}"] = User(env, f"T{i:03}")
        teachers[f"T{i:03}"].activate()
    log("教务、S001–S010、T001–T010 已完成首次改密")

    # ---------------- 教务人员管理 ----------------
    new_accounts = {}
    for label in ("S011", "S012", "S013", "S014"):
        response = registrar.write("POST", "/api/v1/admin/users",
                                   json={"full_name": f"验收学生{label}", "role": "student",
                                         "birth_date": "2005-03-01", "graduation_date": "2029-06-30",
                                         "social_security_number": f"DEMO-ACC-{label}"})
        if response.status_code != 201:
            raise SystemExit(f"创建学生失败：{summarize(response)}")
        new_accounts[label] = response.json()
    numbers = [item["login_number"] for item in new_accounts.values()]
    record("TC-ADM-01", "教务新增 4 名学生", "201，自动分配互不相同的学号",
           f"分配学号 {numbers}", len(set(numbers)) == 4)
    for label, item in new_accounts.items():
        students[label] = User(env, item["login_number"])
        students[label].activate()

    response = registrar.write("POST", "/api/v1/admin/users", json={"full_name": "缺院系教师", "role": "teacher"})
    record("TC-ADM-02", "新增教师未填院系", "422", summarize(response), response.status_code == 422)

    created = registrar.write("POST", "/api/v1/admin/users",
                              json={"full_name": "验收教师甲", "role": "teacher", "department": "计算机学院"})
    teacher_account = created.json()["id"]
    updated = registrar.write("PATCH", f"/api/v1/admin/users/{teacher_account}",
                              json={"full_name": "验收教师乙", "department": "软件学院", "status": "on_leave"})
    fetched = registrar.get(f"/api/v1/admin/users/{teacher_account}").json()
    record("TC-ADM-03", "新增并修改教师资料", "创建 201；修改后查询显示新姓名、院系、状态",
           f"创建 {created.status_code} 工号 {created.json().get('login_number')}；查询 {fetched.get('full_name')}/{fetched.get('department')}/{fetched.get('status')}",
           created.status_code == 201 and updated.status_code == 200
           and fetched.get("full_name") == "验收教师乙" and fetched.get("department") == "软件学院"
           and fetched.get("status") == "on_leave")

    deleted = registrar.write("DELETE", f"/api/v1/admin/users/{teacher_account}")
    missing = registrar.get(f"/api/v1/admin/users/{teacher_account}")
    record("TC-ADM-04", "删除无业务记录的教师", "action=deleted；再次查询 404",
           f"删除 {summarize(deleted)}；查询 {missing.status_code}",
           deleted.json().get("action") == "deleted" and missing.status_code == 404)

    response = registrar.get("/api/v1/admin/users/999999")
    record("TC-ADM-05", "查询不存在的人员", "404", summarize(response), response.status_code == 404)

    html_name = "<b>验收HTML</b>"
    response = registrar.write("POST", "/api/v1/admin/users", json={"full_name": html_name, "role": "student"})
    stored = registrar.get(f"/api/v1/admin/users/{response.json()['id']}").json().get("full_name")
    record("TC-ADM-06", "姓名含 HTML 标签", "按原文保存（页面以文本显示需浏览器另行检查）",
           f"保存值 {stored!r}", stored == html_name)

    s005 = students["S005"]
    response = s005.get("/api/v1/admin/users")
    record("TC-SEC-01", "学生调用教务人员接口", "403", summarize(response), response.status_code == 403)

    # ---------------- 读取验收学期班次 ----------------
    sections = s005.get("/api/v1/student/available-sections").json()
    semester_id = sections["semester_id"]
    ids = {}
    code_of = {}
    for row in sections["sections"]:
        key = row["course_code"] if row["section_number"] == "01" else f"{row['course_code']}-{row['section_number']}"
        ids[key] = row["id"]
        code_of[row["id"]] = key
    log(f"验收学期 {sections['semester_code']}（id={semester_id}）班次：{ids}")
    record("TC-CAT-01", "学生读取外部目录班次", "返回 2027-SPRING 的 11 个班次，均显示描述、院系、名额",
           f"学期 {sections['semester_code']}，班次 {len(sections['sections'])} 个",
           sections["semester_code"] == "2027-SPRING" and len(sections["sections"]) == 11)

    # ---------------- 教师认领 ----------------
    def claim(teacher: str, code: str) -> httpx.Response:
        return teachers[teacher].write("POST", f"/api/v1/teacher/offerings/{ids[code]}/claim")

    response = claim("T001", "ACC101")
    record("TC-TCH-01", "T001 认领有资格的 ACC101", "200，teacher_id 为 T001", summarize(response), response.status_code == 200)
    response = claim("T001", "ACC108")
    record("TC-TCH-02", "T001 认领与 ACC101 同时段的 ACC108", "409，提示与已承担班次时间冲突",
           summarize(response), response.status_code == 409 and "冲突" in response.text)
    response = claim("T002", "ACC101")
    record("TC-TCH-03", "T002 抢占已被认领的 ACC101", "409，提示已被其他教师认领",
           summarize(response), response.status_code == 409 and "其他教师" in response.text)
    response = claim("T009", "ACC102")
    record("TC-TCH-04", "无资格教师 T009 认领 ACC102", "403，提示不具备授课资格",
           summarize(response), response.status_code == 403)
    for teacher, code in (("T002", "ACC102"), ("T003", "ACC103"), ("T004", "ACC104"),
                          ("T004", "ACC104-02"), ("T005", "ACC105"), ("T006", "ACC107"),
                          ("T007", "ACC109"), ("T008", "ACC110")):
        response = claim(teacher, code)
        if response.status_code != 200:
            raise SystemExit(f"{teacher} 认领 {code} 失败：{summarize(response)}")
    released = teachers["T008"].write("POST", f"/api/v1/teacher/offerings/{ids['ACC110']}/release")
    reclaimed = claim("T008", "ACC110")
    record("TC-TCH-05", "T008 取消认领 ACC110 后重新认领", "取消 200 且 teacher_id 为空；重新认领 200",
           f"取消 {summarize(released)}；重新认领 {reclaimed.status_code}",
           released.status_code == 200 and released.json().get("teacher_id") is None and reclaimed.status_code == 200)
    response = teachers["T007"].write("POST", f"/api/v1/teacher/offerings/{ids['ACC110']}/release")
    record("TC-TCH-06", "T007 取消他人承担的 ACC110", "403", summarize(response), response.status_code == 403)
    response = teachers["T001"].get("/semesters/1/schedule")
    record("TC-SEC-02", "教师调用学生方案接口", "403", summarize(response), response.status_code == 403)

    # ---------------- 学生选课 ----------------
    group_a = ["ACC101", "ACC102", "ACC107", "ACC104"]
    alt_ab = ["ACC109", "ACC110"]

    response = save_draft(students["S004"], semester_id, ids, ["ACC101", "ACC102", "ACC103"], alt_ab)
    short = submit(students["S004"], semester_id)
    record("TC-REG-01", "首次提交只有 3 个主选", "422，提示首次必须 4 主选 + 2 备选",
           summarize(short), short.status_code == 422)

    before = seat_counts(s005, semester_id)
    response = save_draft(students["S013"], semester_id, ids, ["ACC101", "ACC102", "ACC103", "ACC104"], alt_ab)
    after = seat_counts(s005, semester_id)
    view = schedule(students["S013"], semester_id)
    record("TC-REG-02", "S013 仅保存草稿", "200；草稿保留 6 项；任何班次人数不变",
           f"保存 {response.status_code}；草稿 {len(view['draft'])} 项；人数变化 {before != after}",
           response.status_code == 200 and len(view["draft"]) == 6 and before == after)

    response = save_and_submit(students["S004"], semester_id, ids, group_a, alt_ab)
    record("TC-REG-03", "S004 未修先修课 FINAL001 选择 ACC107", "409，提示先修课程未满足；无正式结果",
           f"{summarize(response)}；正式结果 {enrolled_codes(students['S004'], semester_id, code_of)}",
           response.status_code == 409 and "先修" in response.text
           and enrolled_codes(students["S004"], semester_id, code_of) == [])

    for label in ("S001", "S002", "S003"):
        response = save_and_submit(students[label], semester_id, ids, group_a, alt_ab)
        if response.status_code != 200:
            raise SystemExit(f"{label} 提交失败：{summarize(response)}")
    record("TC-REG-04", "S001–S003 已修 FINAL001，首次提交含 ACC107 的 4+2 方案",
           "200；正式结果为 4 门主选", f"S001 正式结果 {enrolled_codes(students['S001'], semester_id, code_of)}",
           enrolled_codes(students["S001"], semester_id, code_of) == sorted(group_a))

    response = save_and_submit(students["S004"], semester_id, ids, ["ACC101", "ACC102", "ACC103", "ACC104"], alt_ab)
    if response.status_code != 200:
        raise SystemExit(f"S004 提交失败：{summarize(response)}")
    for label in ("S005", "S006", "S007", "S008", "S009"):
        response = save_and_submit(students[label], semester_id, ids,
                                   ["ACC101", "ACC102", "ACC103", "ACC109"], ["ACC110", "ACC104"])
        if response.status_code != 200:
            raise SystemExit(f"{label} 提交失败：{summarize(response)}")
    counts = seat_counts(s005, semester_id)
    log(f"S001–S009 提交后人数：{counts}")

    # 最后一个名额竞争：S010 与 S011 同时提交含 ACC101 的方案
    race_plan = (["ACC101", "ACC102", "ACC103", "ACC106"], alt_ab)
    for label in ("S010", "S011"):
        save_draft(students[label], semester_id, ids, *race_plan)
    versions = {label: schedule(students[label], semester_id)["version"] for label in ("S010", "S011")}
    outcomes = {}
    barrier = threading.Barrier(2)

    def race(label: str) -> None:
        barrier.wait()
        outcomes[label] = students[label].write(
            "POST", f"/semesters/{semester_id}/schedule/submit", json={"expected_version": versions[label]}
        )

    threads = [threading.Thread(target=race, args=(label,)) for label in ("S010", "S011")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [label for label, item in outcomes.items() if item.status_code == 200]
    losers = [label for label, item in outcomes.items() if item.status_code != 200]
    acc101 = seat_counts(s005, semester_id)["ACC101-01"]
    record("TC-REG-05", "ACC101 剩 1 个名额时 S010、S011 同时提交",
           "仅一人成功，另一人提示已满；ACC101 人数为 10",
           f"S010 {summarize(outcomes['S010'])}；S011 {summarize(outcomes['S011'])}；ACC101 人数 {acc101}",
           len(winners) == 1 and len(losers) == 1 and "已满" in outcomes[losers[0]].text and acc101 == 10)
    winner = students[winners[0]] if winners else students["S010"]
    loser_label = losers[0] if losers else "S011"
    loser = students[loser_label]
    log(f"名额竞争成功者 {winners}，失败者 {losers}")

    response = save_and_submit(loser, semester_id, ids, ["ACC103", "ACC104", "ACC105", "ACC106"], alt_ab)
    original = enrolled_codes(loser, semester_id, code_of)
    record("TC-REG-06", f"{loser_label} 改选未满班次后重新提交", "200；正式结果为 ACC103/ACC104/ACC105/ACC106",
           f"{summarize(response)}；正式结果 {original}",
           response.status_code == 200 and original == ["ACC103", "ACC104", "ACC105", "ACC106"])

    response = save_and_submit(loser, semester_id, ids, ["ACC101", "ACC103", "ACC104", "ACC106"], alt_ab)
    kept = enrolled_codes(loser, semester_id, code_of)
    record("TC-REG-07", f"{loser_label} 后续修改加入已满的 ACC101", "409 已满；原正式结果不变",
           f"{summarize(response)}；正式结果 {kept}",
           response.status_code == 409 and "已满" in response.text and kept == original)

    s012 = students["S012"]
    response = save_and_submit(s012, semester_id, ids, ["ACC101", "ACC108", "ACC103", "ACC104"], alt_ab)
    record("TC-REG-08", "S012 同时选择同一时段的 ACC101 与 ACC108", "409，提示两个班次上课时间冲突；整份提交不生效",
           f"{summarize(response)}；正式结果 {enrolled_codes(s012, semester_id, code_of)}",
           response.status_code == 409 and "冲突" in response.text and enrolled_codes(s012, semester_id, code_of) == [])

    response = save_and_submit(s012, semester_id, ids, ["ACC101", "ACC103", "ACC104", "ACC105"], alt_ab)
    record("TC-REG-09", "S012 首次提交含已满的 ACC101", "409，提示班次已满；无正式结果",
           summarize(response), response.status_code == 409 and "已满" in response.text)

    response = save_and_submit(s012, semester_id, ids, ["ACC103", "ACC104", "ACC105", "ACC109"], ["ACC110", "ACC102"])
    if response.status_code != 200:
        raise SystemExit(f"S012 提交失败：{summarize(response)}")

    response = save_and_submit(students["S013"], semester_id, ids, ["ACC104", "ACC104-02", "ACC103", "ACC109"], ["ACC110", "ACC105"])
    record("TC-REG-10", "S013 主选同一课程的两个班次", "409，提示同一课程只能选择一个班次",
           summarize(response), response.status_code == 409 and "同一课程" in response.text)

    s014 = students["S014"]
    save_and_submit(s014, semester_id, ids, ["ACC103", "ACC104", "ACC109", "ACC110"], ["ACC105", "ACC106"])
    with_s014 = seat_counts(s005, semester_id)["ACC103-01"]
    view = schedule(s014, semester_id)
    deleted = s014.write("DELETE", f"/semesters/{semester_id}/schedule", json={"expected_version": view["version"]})
    without = seat_counts(s005, semester_id)["ACC103-01"]
    record("TC-REG-11", "S014 提交后删除整份方案", "200；正式结果清空；ACC103 人数减 1",
           f"{summarize(deleted)}；ACC103 {with_s014}->{without}；正式结果 {enrolled_codes(s014, semester_id, code_of)}",
           deleted.status_code == 200 and without == with_s014 - 1 and enrolled_codes(s014, semester_id, code_of) == [])

    s002 = students["S002"]
    response = save_and_submit(s002, semester_id, ids, ["ACC101", "ACC102", "ACC107", "ACC104-02"], alt_ab)
    record("TC-REG-12", "S002 将 ACC104 由 01 班换到 02 班", "200；正式结果含 ACC104-02 且不含 ACC104",
           f"{summarize(response)}；正式结果 {enrolled_codes(s002, semester_id, code_of)}",
           response.status_code == 200 and enrolled_codes(s002, semester_id, code_of) == sorted(["ACC101", "ACC102", "ACC107", "ACC104-02"]))

    s003 = students["S003"]
    view = schedule(s003, semester_id)
    dropped = s003.write("DELETE", f"/semesters/{semester_id}/schedule/offerings/{ids['ACC102']}",
                         json={"expected_version": view["version"]})
    view = schedule(s003, semester_id)
    record("TC-REG-13", "S003 单独退选 ACC102", "200；正式结果 3 门，备选未被自动补入",
           f"{summarize(dropped)}；正式结果 {sorted(code_of[r['offering_id']] for r in view['enrolled'])}；剩余备选 {len(view['formal_alternates'])}",
           dropped.status_code == 200 and len(view["enrolled"]) == 3 and len(view["formal_alternates"]) == 2)

    view = schedule(s005, semester_id)
    stale = s005.write("POST", f"/semesters/{semester_id}/schedule/submit", json={"expected_version": view["version"] - 1})
    record("TC-REG-14", "S005 使用旧页面版本提交", "409，提示重新载入；版本不变",
           f"{summarize(stale)}；版本 {schedule(s005, semester_id)['version']}",
           stale.status_code == 409 and "重新载入" in stale.text and schedule(s005, semester_id)["version"] == view["version"])

    response = s005.write("PUT", f"/semesters/{semester_id}/schedule/draft", csrf=False,
                          json={"expected_version": view["version"], "choices": []})
    record("TC-SEC-03", "S005 保存草稿缺少 CSRF 令牌", "403；方案不变", summarize(response), response.status_code == 403)

    roster = teachers["T001"].get(f"/api/v1/teacher/offerings/{ids['ACC101']}/roster").json()
    roster_numbers = sorted(item["student_number"] for item in roster["students"])
    record("TC-TCH-07", "T001 查看 ACC101 花名册", "10 名正式选上的学生，不含只存草稿的 S013",
           f"人数 {len(roster_numbers)}：{roster_numbers}",
           len(roster_numbers) == 10 and students["S013"].username not in roster_numbers)
    response = teachers["T002"].get(f"/api/v1/teacher/offerings/{ids['ACC101']}/roster")
    record("TC-SEC-04", "T002 查看他人班次花名册", "403", summarize(response), response.status_code == 403)
    response = s005.get(f"/api/v1/teacher/offerings/{ids['ACC101']}/roster")
    record("TC-SEC-05", "学生调用教师花名册接口", "403", summarize(response), response.status_code == 403)

    # ---------------- 外部目录故障 ----------------
    env.stop("catalog")
    unavailable = s005.get("/api/v1/student/available-sections", params={"semester_id": semester_id})
    still = s005.get(f"/semesters/{semester_id}/schedule")
    record("TC-CAT-02", "目录服务停止时学生查看课程列表", "503 并提示目录不可用；已保存方案仍可读取",
           f"目录 {summarize(unavailable)}；方案 {still.status_code}",
           unavailable.status_code == 503 and still.status_code == 200 and len(still.json()["enrolled"]) == 4)
    env.start("catalog")
    recovered = s005.get("/api/v1/student/available-sections", params={"semester_id": semester_id})
    record("TC-CAT-03", "目录服务恢复后重新查询", "200", f"HTTP {recovered.status_code}", recovered.status_code == 200)

    # ---------------- 账号停用与重置 ----------------
    s013_account = new_accounts["S013"]["id"]
    registrar.write("PATCH", f"/api/v1/admin/users/{s013_account}/status", json={"is_active": False})
    blocked = students["S013"].get(f"/semesters/{semester_id}/schedule")
    login_disabled = User(env, students["S013"].username).login(NEW_PASSWORD)
    registrar.write("PATCH", f"/api/v1/admin/users/{s013_account}/status", json={"is_active": True})
    enabled = students["S013"].login(NEW_PASSWORD)
    record("TC-ADM-07", "停用 S013 后旧会话访问、再启用", "停用后旧会话 401、不能登录；启用后可登录",
           f"旧会话 {blocked.status_code}；停用期间登录 {login_disabled.status_code}；启用后登录 {enabled.status_code}",
           blocked.status_code == 401 and login_disabled.status_code in (401, 403) and enabled.status_code == 200)

    # ---------------- 关闭选课 ----------------
    response = s005.write("POST", "/api/v1/admin/registration/close",
                          json={"semester_id": semester_id, "confirmation": "CLOSE-CONFIRM"})
    record("TC-SEC-06", "学生调用关闭选课", "403", summarize(response), response.status_code == 403)
    response = registrar.write("POST", "/api/v1/admin/registration/close",
                               json={"semester_id": semester_id, "confirmation": "close"})
    record("TC-CLS-01", "关闭确认口令错误", "422；学期仍开放", summarize(response), response.status_code == 422)

    env.stop("billing")  # 关闭时计费系统不可用，验证关闭与外部发送分离
    closed = registrar.write("POST", "/api/v1/admin/registration/close",
                             json={"semester_id": semester_id, "confirmation": "CLOSE-CONFIRM"})
    cancelled = sorted(code_of[item] for item in closed.json().get("cancelled_offering_ids", []))
    expected_cancelled = sorted(["ACC104-02", "ACC105", "ACC106", "ACC108", "ACC110"])
    record("TC-CLS-02", "计费系统停止时教务关闭选课",
           f"200；取消 {expected_cancelled}；创建 12 个账单任务",
           f"{closed.status_code}；取消 {cancelled}；账单任务 {closed.json().get('billing_jobs_created')}",
           closed.status_code == 200 and cancelled == expected_cancelled and closed.json().get("billing_jobs_created") == 12)

    expected = {
        "S001": (["ACC101", "ACC102", "ACC104", "ACC107"], 450.0),
        "S002": (["ACC101", "ACC102", "ACC107", "ACC109"], 430.0),
        "S003": (["ACC101", "ACC104", "ACC107"], 300.0),
        "S004": (["ACC101", "ACC102", "ACC103", "ACC104"], 450.0),
        winners[0] if winners else "S010": (["ACC101", "ACC102", "ACC103", "ACC109"], 430.0),
        loser_label: (["ACC103", "ACC104", "ACC109"], 280.0),
        "S012": (["ACC102", "ACC103", "ACC104", "ACC109"], 430.0),
    }
    for label in ("S005", "S006", "S007", "S008", "S009"):
        expected[label] = (["ACC101", "ACC102", "ACC103", "ACC109"], 430.0)
    status = registrar.get("/api/v1/admin/registration/closing-status", params={"semester_id": semester_id}).json()
    bills = {item["student_number"]: item for item in status["bills"]}
    mismatches = []
    for label, (codes, amount) in expected.items():
        number = students[label].username
        actual_codes = enrolled_codes(students[label], semester_id, code_of)
        bill = bills.get(number)
        if actual_codes != codes or bill is None or bill["amount"] != amount or bill["enrolled_count"] != len(codes):
            mismatches.append(f"{label}: 课表 {actual_codes} 账单 {bill and (bill['amount'], bill['enrolled_count'])}")
    no_bill = [label for label in ("S013", "S014") if students[label].username in bills]
    record("TC-CLS-03", "核对关闭后每位学生课表与金额快照",
           "12 名学生课表与金额与预期一致（含两轮备选替补）；只存草稿的 S013、已删除方案的 S014 无账单",
           f"不一致 {mismatches or '无'}；不应有账单却有 {no_bill or '无'}；总额 {status['total_amount']}",
           not mismatches and not no_bill and len(bills) == 12)
    record("TC-CLS-04", "S002 换入的 ACC104-02 人数不足被取消后由备选替补",
           "S002 获得第一备选 ACC109", f"S002 正式结果 {enrolled_codes(s002, semester_id, code_of)}",
           "ACC109" in enrolled_codes(s002, semester_id, code_of))
    record("TC-CLS-05", "主动退课的 S003 关闭时不自动补回", "S003 仍为 3 门",
           f"S003 正式结果 {enrolled_codes(s003, semester_id, code_of)}", len(enrolled_codes(s003, semester_id, code_of)) == 3)

    response = save_and_submit(s005, semester_id, ids, ["ACC101", "ACC102", "ACC103"], [])
    record("TC-CLS-06", "关闭后学生修改方案", "409，提示选课已关闭", summarize(response),
           response.status_code == 409 and "关闭" in response.text)
    response = teachers["T010"].write("POST", f"/api/v1/teacher/offerings/{ids['ACC106']}/claim")
    record("TC-CLS-07", "关闭后教师认领班次", "409，提示学期不再开放", summarize(response), response.status_code == 409)
    again = registrar.write("POST", "/api/v1/admin/registration/close",
                            json={"semester_id": semester_id, "confirmation": "CLOSE-CONFIRM"})
    record("TC-CLS-08", "重复关闭同一学期", "200；不重复创建账单任务（仍为 12）", summarize(again),
           again.status_code == 200 and again.json().get("billing_jobs_created") == 12)

    # ---------------- 计费发送与恢复 ----------------
    # 关闭后立即派发：BUG-01 修复前 MySQL 时间被进位到下一秒，这里会偶发取到 0 条。
    dispatch = registrar.write("POST", "/api/v1/registrar/billing/dispatch")
    status = registrar.get("/api/v1/admin/registration/closing-status", params={"semester_id": semester_id}).json()
    record("TC-BIL-01", "计费系统不可用时发送账单", "12 条均失败并等待重试；状态 PARTIAL_FAIL",
           f"派发 {summarize(dispatch)}；状态 {status['billing_status']} 失败 {status['jobs_failed']}",
           status["jobs_failed"] == 12 and status["billing_status"] == "PARTIAL_FAIL")

    env.start("billing")
    time.sleep(1.5)
    dispatch = registrar.write("POST", "/api/v1/registrar/billing/dispatch")
    status = registrar.get("/api/v1/admin/registration/closing-status", params={"semester_id": semester_id}).json()
    charges = httpx.get(f"{env.billing_url}/billing/charges").json()
    record("TC-BIL-02", "计费系统恢复后重试", "12 条全部发送；ALL_SENT；计费端 12 笔且金额与快照一致",
           f"派发 {summarize(dispatch)}；状态 {status['billing_status']}；计费端 {charges['count']} 笔 合计 {sum(float(c['amount']) for c in charges['charges'])}",
           status["billing_status"] == "ALL_SENT" and charges["count"] == 12
           and abs(sum(float(c["amount"]) for c in charges["charges"]) - status["total_amount"]) < 0.001)

    first_job = status["bills"][0]["bill_id"]
    response = registrar.write("POST", f"/api/v1/registrar/billing/jobs/{first_job}/retry")
    record("TC-BIL-03", "对已发送账单重新排队", "409，提示已发送成功", summarize(response), response.status_code == 409)

    sample = charges["charges"][0]
    payload = {"semester_id": sample["semester_id"], "student_id": sample["student_id"],
               "amount": sample["amount"], "schedule_snapshot": sample["schedule_snapshot"]}
    duplicate = httpx.post(f"{env.billing_url}/billing/charges", json=payload)
    conflict = httpx.post(f"{env.billing_url}/billing/charges", json={**payload, "amount": "1.00"})
    count = httpx.get(f"{env.billing_url}/billing/charges").json()["count"]
    record("TC-BIL-04", "向计费端重复发送同一账单及内容不同的账单",
           "相同内容 200 duplicate=true；不同金额 409；账单仍为 12 笔",
           f"重复 {summarize(duplicate)[:80]}；冲突 {conflict.status_code}；笔数 {count}",
           duplicate.status_code == 200 and duplicate.json()["duplicate"] is True and conflict.status_code == 409 and count == 12)

    env.stop("billing")
    env.start("billing")
    after_restart = httpx.get(f"{env.billing_url}/billing/charges").json()["count"]
    duplicate = httpx.post(f"{env.billing_url}/billing/charges", json=payload)
    record("TC-BIL-05", "计费端重启后再次接收同一账单", "账本保留 12 笔；重复发送仍识别为重复",
           f"重启后 {after_restart} 笔；重复发送 duplicate={duplicate.json().get('duplicate')}",
           after_restart == 12 and duplicate.json().get("duplicate") is True)

    # ---------------- 成绩 ----------------
    t001 = teachers["T001"]
    gradable = t001.get("/api/v1/teacher/gradable-sections").json()
    history = gradable["offerings"][0]["offering_id"] if gradable["offerings"] else None
    record("TC-GRD-01", "T001 查看可录成绩的上一已完成学期班次", "2025-FALL 的 FINAL001 班次",
           f"学期 {gradable['semester_code']}，班次 {[o['course_code'] for o in gradable['offerings']]}",
           gradable["semester_code"] == "2025-FALL" and history is not None)

    sheet = t001.get(f"/api/v1/teacher/offerings/{history}/grades").json()
    by_number = {item["student_number"]: item["student_id"] for item in sheet["students"]}
    entries = [{"student_id": by_number["S001"], "value": "A"},
               {"student_id": by_number["S002"], "value": None},
               {"student_id": by_number["S003"], "value": "I"}]
    saved = t001.write("PUT", f"/api/v1/teacher/offerings/{history}/grades", json={"grades": entries})
    changes = t001.get(f"/api/v1/teacher/offerings/{history}/grade-changes").json()
    record("TC-GRD-02", "T001 修改 S001 为 A、清空 S002、S003 记为 I", "200，updated=3；新增 3 条变更记录",
           f"{summarize(saved)[:120]}；变更记录 {changes['total']} 条",
           saved.status_code == 200 and saved.json().get("updated") == 3 and changes["total"] >= 3)

    own = students["S001"].get("/api/v1/student/me/grades").json()
    rows = own.get("grades", own if isinstance(own, list) else [])
    values = [row.get("value") for row in rows if row.get("semester_code") == "2025-FALL"]
    record("TC-GRD-03", "S001 查看本人成绩单", "2025-FALL FINAL001 成绩为 A，只含本人记录",
           f"返回 {json.dumps(own, ensure_ascii=False)[:160]}", values == ["A"])

    response = teachers["T002"].write("PUT", f"/api/v1/teacher/offerings/{history}/grades",
                                      json={"grades": [{"student_id": by_number["S001"], "value": "F"}]})
    record("TC-GRD-04", "T002 修改他人班次成绩", "403", summarize(response), response.status_code == 403)

    outsider = {item["student_number"]: item["student_id"] for item in roster["students"]}[students["S004"].username]
    response = t001.write("PUT", f"/api/v1/teacher/offerings/{history}/grades",
                          json={"grades": [{"student_id": outsider, "value": "B"}]})
    record("TC-GRD-05", "T001 给不在该班名单的 S004 录成绩", "403", summarize(response), response.status_code == 403)

    response = t001.write("PUT", f"/api/v1/teacher/offerings/{ids['ACC101']}/grades",
                          json={"grades": [{"student_id": outsider, "value": "A"}]})
    record("TC-GRD-06", "给已关闭但未结束的 2027-SPRING 班次录成绩", "409，提示只能录入已完成学期",
           summarize(response), response.status_code == 409 and "已完成" in response.text)

    response = t001.write("PUT", f"/api/v1/teacher/offerings/{history}/grades",
                          json={"grades": [{"student_id": by_number["S001"], "value": "E"}]})
    record("TC-GRD-07", "录入非法成绩 E", "422", summarize(response), response.status_code == 422)

    response = s005.write("PUT", f"/api/v1/teacher/offerings/{history}/grades",
                          json={"grades": [{"student_id": by_number["S001"], "value": "A"}]})
    record("TC-SEC-07", "学生调用成绩录入接口", "403", summarize(response), response.status_code == 403)

    # ---------------- 有历史的人员删除与重置密码 ----------------
    s014_account = new_accounts["S014"]["id"]
    deleted = registrar.write("DELETE", f"/api/v1/admin/users/{s014_account}")
    record("TC-ADM-08", "删除有选课历史的 S014", "action=deactivated（保留历史，转为停用）",
           summarize(deleted), deleted.json().get("action") == "deactivated")

    s012_account = new_accounts["S012"]["id"]
    reset = registrar.write("POST", f"/api/v1/admin/users/{s012_account}/reset-password")
    login = User(env, students["S012"].username).login(INITIAL)
    record("TC-ADM-09", "重置 S012 密码", "200；可用初始密码登录且必须改密",
           f"重置 {reset.status_code}；登录 {summarize(login)}",
           reset.status_code == 200 and login.status_code == 200 and login.json()["is_first_login"] is True)

    old_cookie = probe.client.cookies.get("session_id")
    response = probe.write("POST", "/api/v1/auth/logout")
    after = httpx.get(f"{env.base}/semesters/{semester_id}/schedule", cookies={"session_id": old_cookie})
    record("TC-AUTH-07", "退出登录后访问", "退出 200；之后 401", f"退出 {response.status_code}；访问 {after.status_code}",
           response.status_code == 200 and after.status_code == 401)


if __name__ == "__main__":
    main()
