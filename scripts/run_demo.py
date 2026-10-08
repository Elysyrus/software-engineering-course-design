"""python -m scripts.run_demo：初始化并启动主应用、目录、计费和重试进程。"""

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(
        description="启动完整课程注册系统，Ctrl+C 停止本次启动的进程"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./course_registration.db"),
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--catalog-port", type=int, default=8081)
    parser.add_argument("--billing-port", type=int, default=8082)
    parser.add_argument(
        "--billing-database", default="mock_billing.db", help="独立计费账本路径"
    )
    parser.add_argument(
        "--no-seed", action="store_true", help="只升级数据库，不创建演示账号和数据"
    )
    args = parser.parse_args()
    if len({args.port, args.catalog_port, args.billing_port}) != 3:
        parser.error("三个服务端口必须不同")
    for port in (args.port, args.catalog_port, args.billing_port):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                parser.error(f"端口 {port} 已被占用，请停止旧进程或选择其他端口")
    environment = {
        **os.environ,
        "DATABASE_URL": args.database_url,
        "APP_ENV": "development",
        "PYTHONUTF8": "1",
        "CATALOG_SERVICE_URL": f"http://127.0.0.1:{args.catalog_port}",
        "BILLING_SERVICE_URL": f"http://127.0.0.1:{args.billing_port}",
        "MOCK_BILLING_DATABASE": str((ROOT / args.billing_database).resolve()),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    if not args.no_seed:
        subprocess.run(
            [sys.executable, "-m", "scripts.seed_final_demo"],
            cwd=ROOT,
            env=environment,
            check=True,
        )
    children = []
    logs = []
    logdir = ROOT / "tmp" / f"run-demo-{args.port}"
    logdir.mkdir(parents=True, exist_ok=True)
    commands = [
        (
            "catalog",
            [
                "-m",
                "uvicorn",
                "mock_catalog.mock_catalog_service:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.catalog_port),
            ],
        ),
        (
            "billing",
            [
                "-m",
                "uvicorn",
                "mock_billing.mock_billing_service:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.billing_port),
            ],
        ),
        ("worker", ["-m", "scripts.billing_retry_worker"]),
        (
            "app",
            [
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
        ),
    ]
    try:
        for name, command in commands:
            log = (logdir / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            children.append(
                (
                    name,
                    subprocess.Popen(
                        [sys.executable, *command],
                        cwd=ROOT,
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    ),
                )
            )
        print(
            f"打开 http://127.0.0.1:{args.port}/login\n教务 registrar；学生 S001–S010；教师 T001–T010\n初始密码 Initial123，首次登录必须改密。\n日志：{logdir}\nCtrl+C 停止所有本次启动的服务。",
            flush=True,
        )
        while True:
            for name, process in children:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} 进程退出，见 {logdir/name}.log")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("正在停止服务…", flush=True)
    finally:
        for _, process in reversed(children):
            if process.poll() is None:
                process.terminate()
        for _, process in children:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
