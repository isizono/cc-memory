"""hooks/pretooluse_hook.py のE2Eテスト（イベント駆動アーキテクチャ版）

subprocess.runでpretooluse_hook.pyを呼び出し、stdin→stdoutの入出力をテスト。
nudge判定はevents.jsonl内のnudgeイベントに基づく。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hooks.hook_state import HookState

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SESSION_ID = "e2e-test-session-001"


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """テスト用のstateディレクトリを返し、HookStateのBASE_DIRもオーバーライド"""
    monkeypatch.setattr(HookState, "BASE_DIR", tmp_path)
    return tmp_path


def _run_hook(input_data: dict, state_dir: Path) -> subprocess.CompletedProcess:
    """pretooluse_hook.pyをサブプロセスで実行する"""
    return subprocess.run(
        [sys.executable, "hooks/pretooluse_hook.py"],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
        env={**os.environ, "HOOK_STATE_DIR": str(state_dir)},
    )


def _write_events(events: list[dict], state_dir: Path) -> None:
    """events.jsonlをpre-seedする"""
    state = HookState(_SESSION_ID)
    state.append_events(events)


class TestNoNudge:
    """nudgeイベントなし → 空JSON"""

    def test_empty_json_when_no_events(self, state_dir):
        result = _run_hook({"session_id": _SESSION_ID}, state_dir)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_empty_json_when_no_nudge_events(self, state_dir):
        _write_events(
            [
                {"e": "tool", "name": "get_topics", "turn": 1},
                {"e": "meta", "topic": "test", "turn": 1},
            ],
            state_dir,
        )
        result = _run_hook({"session_id": _SESSION_ID}, state_dir)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}


class TestRecordNudge:
    """record nudgeイベント → system-reminder注入"""

    def test_record_nudge_injection(self, state_dir):
        _write_events(
            [{"e": "nudge", "type": "record", "turn": 2}],
            state_dir,
        )

        result = _run_hook({"session_id": _SESSION_ID}, state_dir)
        assert result.returncode == 0

        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "<system-reminder>" in ctx
        assert "記録が遅れています" in ctx
        assert "add_decisions" in ctx

    def test_nudge_consumed_after_injection(self, state_dir):
        """nudge消費後は空JSON"""
        _write_events(
            [{"e": "nudge", "type": "record", "turn": 2}],
            state_dir,
        )

        _run_hook({"session_id": _SESSION_ID}, state_dir)

        # 2回目は空JSON
        result = _run_hook({"session_id": _SESSION_ID}, state_dir)
        assert json.loads(result.stdout) == {}


class TestFollowUpNudge:
    """follow_up nudgeイベント → system-reminder注入"""

    def test_follow_up_nudge_injection(self, state_dir):
        _write_events(
            [{"e": "nudge", "type": "follow_up", "turn": 3}],
            state_dir,
        )

        result = _run_hook({"session_id": _SESSION_ID}, state_dir)
        assert result.returncode == 0

        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "決定事項" in ctx
        assert "topic" in ctx

    def test_follow_up_nudge_takes_priority(self, state_dir):
        """follow_up nudgeが最新なら、record nudgeより優先"""
        _write_events(
            [
                {"e": "nudge", "type": "record", "turn": 2},
                {"e": "nudge", "type": "follow_up", "turn": 3},
            ],
            state_dir,
        )

        result = _run_hook({"session_id": _SESSION_ID}, state_dir)
        output = json.loads(result.stdout)
        ctx = output["hookSpecificOutput"]["additionalContext"]
        # follow_up nudgeが注入される（最新のnudgeが先に消費される）
        assert "決定事項" in ctx

        # record nudgeはまだ残っている
        result2 = _run_hook({"session_id": _SESSION_ID}, state_dir)
        output2 = json.loads(result2.stdout)
        ctx2 = output2["hookSpecificOutput"]["additionalContext"]
        assert "記録が遅れています" in ctx2


class TestEmptySessionId:
    """session_id空 → 空JSON"""

    def test_empty_session_id(self, state_dir):
        result = _run_hook({"session_id": ""}, state_dir)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_null_session_id(self, state_dir):
        result = _run_hook({"session_id": None}, state_dir)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}


class TestFailOpen:
    """例外→空JSON（フェイルオープン）"""

    def test_invalid_json_input(self, state_dir):
        proc = subprocess.run(
            [sys.executable, "hooks/pretooluse_hook.py"],
            input="not valid json",
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_ROOT),
            env={**os.environ, "HOOK_STATE_DIR": str(state_dir)},
        )
        assert proc.returncode == 0
        assert json.loads(proc.stdout) == {}
        assert "error" in proc.stderr.lower()
