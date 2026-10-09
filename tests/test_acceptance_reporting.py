"""验收失败或中断必须让命令失败，并清楚列出未执行用例。"""

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["complete", "partial", "failed", "interrupted"])
def test_acceptance_exit_and_saved_summary(tmp_path, monkeypatch, mode):
    path = (
        Path(__file__).resolve().parents[1]
        / "docs/测试与交付/验收工具/run_acceptance.py"
    )
    spec = importlib.util.spec_from_file_location("acceptance_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.Environment, "prepare", lambda self: None)
    monkeypatch.setattr(module.Environment, "shutdown", lambda self: None)

    def run(_env):
        ids = sorted(module.EXPECTED_CASE_IDS)
        if mode == "partial":
            ids = ids[:51]
        for index, case_id in enumerate(ids):
            module.record(
                case_id, "test", "ok", "ok", not (mode == "failed" and index == 0)
            )
        if mode == "interrupted":
            raise RuntimeError("服务连接超时")

    monkeypatch.setattr(module, "run_cases", run)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            str(path),
            "--database-url",
            "mysql+pymysql://localhost/acceptance_test",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert module.main() == (0 if mode == "complete" else 1)
    import json

    result = json.loads(
        (tmp_path / "acceptance_results.json").read_text(encoding="utf-8")
    )
    assert result["expected_total"] == 68
    assert result["completed"] == (mode in {"complete", "failed"})
    assert result["failed"] == (1 if mode == "failed" else 0)
    assert len(result["missing_case_ids"]) == (17 if mode == "partial" else 0)
    if mode == "interrupted":
        assert "服务连接超时" in result["error"]
