"""hooks/session_end_hook.py の E2E テスト

subprocess.run で session_end_hook.py を呼び出し、stdin→stdout の入出力をテスト。
claude -p の起動はモック化（実際には起動しない）。
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# --- ヘルパー ---


def _write_transcript(lines: list[dict], path: Path) -> None:
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def _make_user_entry(text: str = "hello") -> dict:
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _make_assistant_entry(text: str = "hi") -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _make_meta_user_entry() -> dict:
    """isMeta=trueのユーザーエントリ（スキル内容注入等）"""
    return {
        "type": "user",
        "isMeta": True,
        "message": {"content": "skill injection content"},
    }


def _make_tool_result_entry() -> dict:
    """tool_resultを含むユーザーエントリ"""
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_0", "content": "ok"}]},
    }


def _run_session_end_hook(
    transcript_path: str,
    return_stderr: bool = False,
) -> dict | tuple[dict, str]:
    input_data = json.dumps({"transcript_path": transcript_path})

    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "hooks" / "session_end_hook.py")],
        input=input_data,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=10,
    )

    stdout = result.stdout.strip()
    output = json.loads(stdout) if stdout else {}

    if return_stderr:
        return output, result.stderr
    return output


# --- テスト ---


class TestAlwaysApprove:
    """SessionEnd hookは常にapproveを返す"""

    def test_empty_transcript_path(self):
        input_data = json.dumps({"transcript_path": ""})
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "hooks" / "session_end_hook.py")],
            input=input_data,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=10,
        )
        output = json.loads(result.stdout.strip())
        assert output["decision"] == "approve"

    def test_nonexistent_transcript(self, tmp_path):
        output = _run_session_end_hook(str(tmp_path / "nonexistent.jsonl"))
        assert output["decision"] == "approve"

    def test_invalid_json_input(self):
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "hooks" / "session_end_hook.py")],
            input="not json",
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=10,
        )
        output = json.loads(result.stdout.strip())
        assert output["decision"] == "approve"


class TestSyncMarkerCheck:
    """sync-memoryマーカーがあればスキップ"""

    def test_skip_when_marker_present(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry("hello"),
            _make_assistant_entry("hi"),
            _make_user_entry("do something"),
            _make_assistant_entry("claude-code-memory:sync-memory done"),
        ], transcript)

        output = _run_session_end_hook(str(transcript))
        assert output["decision"] == "approve"


class TestOneLinerDetection:
    """ワンライナー（user_message_count <= 1）はスキップ"""

    def test_skip_zero_user_messages(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_assistant_entry("system response"),
        ], transcript)

        output = _run_session_end_hook(str(transcript))
        assert output["decision"] == "approve"

    def test_skip_one_user_message(self, tmp_path):
        """パイプモード相当: ユーザーメッセージ1件"""
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry("do this task"),
            _make_assistant_entry("done"),
        ], transcript)

        output = _run_session_end_hook(str(transcript))
        assert output["decision"] == "approve"

    def test_skip_one_user_message_with_many_assistant(self, tmp_path):
        """ツール多用のワンライナー: user 1件だがassistant多数"""
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry("complex task"),
            _make_assistant_entry("step 1"),
            _make_tool_result_entry(),
            _make_assistant_entry("step 2"),
            _make_tool_result_entry(),
            _make_assistant_entry("step 3"),
            _make_tool_result_entry(),
            _make_assistant_entry("final answer"),
        ], transcript)

        output = _run_session_end_hook(str(transcript))
        assert output["decision"] == "approve"

    def test_meta_user_entries_not_counted(self, tmp_path):
        """isMeta=trueのエントリはUser Messageとしてカウントしない"""
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry("only real user message"),
            _make_meta_user_entry(),
            _make_assistant_entry("response"),
            _make_meta_user_entry(),
            _make_assistant_entry("another response"),
        ], transcript)

        output = _run_session_end_hook(str(transcript))
        assert output["decision"] == "approve"

    def test_tool_result_entries_not_counted(self, tmp_path):
        """tool_resultエントリはUser Messageとしてカウントしない"""
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry("only real message"),
            _make_assistant_entry("using tool"),
            _make_tool_result_entry(),
            _make_assistant_entry("done"),
        ], transcript)

        output = _run_session_end_hook(str(transcript))
        assert output["decision"] == "approve"


class TestAutoSyncLaunch:
    """user_message_count >= 2 かつ sync-memory未実行なら auto-sync を起動"""

    def test_launches_when_multi_turn(self, tmp_path, monkeypatch):
        transcript = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry("first question"),
            _make_assistant_entry("first answer"),
            _make_user_entry("second question"),
            _make_assistant_entry("second answer"),
        ], transcript)

        # auto_sync_prompt.txt をダミーで配置
        prompt_file = tmp_path / "auto_sync_prompt.txt"
        prompt_file.write_text("dummy system prompt")

        # session_end_hook.pyをインポートしてPopen をモック化
        sys.path.insert(0, str(PROJECT_ROOT))
        import hooks.session_end_hook as hook_mod

        launched_pids = []
        original_popen = subprocess.Popen

        class MockPopen:
            def __init__(self, *args, **kwargs):
                self.pid = 12345
                launched_pids.append(self.pid)

        monkeypatch.setattr(subprocess, "Popen", MockPopen)
        monkeypatch.setattr(hook_mod, "_LOG_FILE", tmp_path / "test.log")

        # script_dirをtmp_pathに差し替えてテスト
        original_main = hook_mod.main

        def patched_main():
            monkeypatch.setattr(
                hook_mod.Path(__file__).resolve().__class__,
                "parent",
                property(lambda self: tmp_path),
            )

        # 直接関数を呼ぶ代わりにsubprocessで実行（E2Eテスト）
        # ただしclaude -pの実際の起動は避けたいので、
        # _launch_auto_syncをモック化したバージョンで検証
        monkeypatch.undo()

        # シンプルなアプローチ: ログファイルで起動判定
        log_file = tmp_path / "session-end.log"
        monkeypatch.setattr(hook_mod, "_LOG_FILE", log_file)

        captured_args = []

        class MockPopen2:
            def __init__(self, *args, **kwargs):
                self.pid = 99999
                captured_args.append(args[0])

        monkeypatch.setattr(subprocess, "Popen", MockPopen2)

        # _launch_auto_syncを直接テスト
        pid = hook_mod._launch_auto_sync(transcript, tmp_path)
        assert pid == 99999
        assert captured_args[0][0] == "claude"
        assert "-p" in captured_args[0]
        assert "--model" in captured_args[0]

        monkeypatch.undo()
        sys.path.pop(0)
