"""初始化唯一教务账号。

从项目根目录运行：
    ./.venv/Scripts/python.exe -m scripts.init_registrar --login-number registrar
"""

import argparse
import getpass
import os
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.personnel import init_registrar


def initialize_registrar(
    login_number: str,
    initial_password: str,
    session_factory: Callable[[], Session] = SessionLocal,
) -> tuple[int, str]:
    """创建或读取唯一教务账号；返回账号 ID 和实际登录编号。"""
    with session_factory() as db:
        existing = init_registrar(db, login_number, initial_password)
        # 若同角色账号已存在，init_registrar 会原样返回；编号可用于人工确认。
        return existing.id, existing.login_number


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化课程注册系统教务账号")
    parser.add_argument("--login-number", default="registrar", help="教务登录编号，默认 registrar")
    args = parser.parse_args()

    # 优先读取环境变量，便于非交互部署；否则安全地在终端输入，不回显密码。
    password = os.getenv("INITIAL_REGISTRAR_PASSWORD")
    if password is None:
        password = getpass.getpass("请输入教务初始密码：")

    account_id, actual_login_number = initialize_registrar(
        args.login_number,
        password,
    )
    print(f"教务账号已就绪：id={account_id}，登录编号={actual_login_number}")
    print("请使用该账号登录；首次登录后必须修改密码。")


if __name__ == "__main__":
    main()
