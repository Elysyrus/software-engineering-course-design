"""后台计费重试工作进程。

关闭选课只负责把账单任务写进本地数据库，真正的发送与重试由本进程按固定间隔执行：

    ./.venv/Scripts/python.exe -m scripts.billing_retry_worker

参数：

    --interval 5   轮询间隔秒数，默认 5；单条账单最早何时重发仍由
                   ``BILLING_RETRY_SECONDS``（默认 30 秒）决定
    --once         只执行一轮就退出，便于演示与排查

按 Ctrl+C 停止；未发送成功的任务仍留在数据库里，下次启动继续处理。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from app.services.billing import run_retry_loop


logger = logging.getLogger("billing_retry_worker")


def _log_cycle(summary: dict) -> None:
    if summary["dispatched"] == 0:
        logger.debug("本轮没有到期的账单任务")
        return
    logger.info(
        "本轮派发 %s 条账单：成功 %s，失败 %s（其中停止重试 %s）",
        summary["dispatched"],
        summary["sent"],
        summary["failed"],
        summary["abandoned"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="后台计费账单发送与重试进程")
    parser.add_argument(
        "--interval", type=float, default=5.0, help="轮询间隔秒数，默认 5"
    )
    parser.add_argument("--once", action="store_true", help="只执行一轮后退出")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    stop_event = threading.Event()

    def _stop(_signum, _frame) -> None:
        logger.info("收到停止信号，等待本轮结束后退出")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):  # 非主线程或不支持该信号的平台
            pass

    result = run_retry_loop(
        interval_seconds=args.interval,
        iterations=1 if args.once else None,
        stop_event=stop_event,
        on_cycle=_log_cycle,
    )
    logger.info("计费重试进程结束：%s", result)


if __name__ == "__main__":
    main()
