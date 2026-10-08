"""先启动目录服务，再执行 python -m scripts.sync_catalog。"""

from app.database import SessionLocal
from app.services.catalog import fetch_catalog, import_catalog


def main():
    snapshot = fetch_catalog()
    with SessionLocal() as db:
        semester = import_catalog(db, snapshot)
        print(f"已同步只读目录：{semester.code}")


if __name__ == "__main__":
    main()
