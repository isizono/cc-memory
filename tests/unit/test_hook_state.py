"""hooks/hook_state.py のユニットテスト"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hooks.hook_state import HookState


@pytest.fixture
def hook_state(tmp_path, monkeypatch):
    monkeypatch.setattr(HookState, "BASE_DIR", tmp_path)
    return HookState("test-session-123")


class TestPrevTopic:
    def test_get_returns_none_when_no_file(self, hook_state):
        assert hook_state.get_prev_topic() is None

    def test_set_then_get(self, hook_state):
        hook_state.set_prev_topic(42)
        assert hook_state.get_prev_topic() == 42

    def test_corrupted_file_returns_none(self, hook_state):
        path = hook_state._path("prev_topic")
        path.write_text("not-a-number")
        assert hook_state.get_prev_topic() is None


class TestBlockCount:
    def test_get_returns_zero_when_no_file(self, hook_state):
        assert hook_state.get_block_count() == 0

    def test_increment(self, hook_state):
        assert hook_state.increment_block_count() == 1
        assert hook_state.increment_block_count() == 2

    def test_reset_then_get(self, hook_state):
        hook_state.increment_block_count()
        hook_state.increment_block_count()
        hook_state.reset_block_count()
        assert hook_state.get_block_count() == 0

    def test_corrupted_file_returns_zero(self, hook_state):
        path = hook_state._path("block_count")
        path.write_text("abc")
        assert hook_state.get_block_count() == 0


class TestNudgeCounter:
    def test_get_returns_zero_when_no_file(self, hook_state):
        assert hook_state.get_nudge_counter() == 0

    def test_increment(self, hook_state):
        assert hook_state.increment_nudge_counter() == 1
        assert hook_state.increment_nudge_counter() == 2

    def test_reset(self, hook_state):
        hook_state.increment_nudge_counter()
        hook_state.reset_nudge_counter()
        assert hook_state.get_nudge_counter() == 0

    def test_corrupted_file_returns_zero(self, hook_state):
        path = hook_state._path("nudge_counter")
        path.write_text("xyz")
        assert hook_state.get_nudge_counter() == 0


class TestNudgePending:
    def test_pop_returns_false_when_no_file(self, hook_state):
        assert hook_state.pop_nudge_pending() is False

    def test_set_then_pop(self, hook_state):
        hook_state.set_nudge_pending()
        assert hook_state.pop_nudge_pending() is True

    def test_pop_after_pop_returns_false(self, hook_state):
        hook_state.set_nudge_pending()
        hook_state.pop_nudge_pending()
        assert hook_state.pop_nudge_pending() is False


class TestNudgeTopicName:
    def test_pop_returns_none_when_no_file(self, hook_state):
        assert hook_state.pop_nudge_topic_name() is None

    def test_set_then_pop(self, hook_state):
        hook_state.set_nudge_topic_name(55, "Test Topic")
        result = hook_state.pop_nudge_topic_name()
        assert result == {"topic_id": 55, "actual_name": "Test Topic"}

    def test_pop_after_pop_returns_none(self, hook_state):
        hook_state.set_nudge_topic_name(55, "Test Topic")
        hook_state.pop_nudge_topic_name()
        assert hook_state.pop_nudge_topic_name() is None

    def test_corrupted_json_returns_none(self, hook_state):
        path = hook_state._path("nudge_topic_name")
        path.write_text("{invalid json")
        assert hook_state.pop_nudge_topic_name() is None
        # ファイルも削除されている
        assert not path.exists()


class TestClearSession:
    def test_clears_all_state_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(HookState, "BASE_DIR", tmp_path)
        state = HookState("sess-abc")

        # 全種類の状態ファイルを作成
        state.set_prev_topic(10)
        state.increment_block_count()
        state.increment_nudge_counter()
        state.set_nudge_pending()
        state.set_nudge_topic_name(5, "name")

        # clear
        HookState.clear_session("sess-abc")

        # 全ファイルが消えている
        assert state.get_prev_topic() is None
        assert state.get_block_count() == 0
        assert state.get_nudge_counter() == 0
        assert state.pop_nudge_pending() is False
        assert state.pop_nudge_topic_name() is None


class TestSessionIdSlash:
    def test_slash_replaced_with_underscore(self, tmp_path, monkeypatch):
        monkeypatch.setattr(HookState, "BASE_DIR", tmp_path)
        state = HookState("user/session/123")
        state.set_prev_topic(99)

        # ファイル名に '/' が含まれず '_' に置換されている
        expected_file = tmp_path / "prev_topic_user_session_123"
        assert expected_file.exists()
        assert expected_file.read_text().strip() == "99"


class TestMainCli:
    def test_clear_via_cli(self, tmp_path, monkeypatch):
        monkeypatch.setattr(HookState, "BASE_DIR", tmp_path)

        # 状態ファイルを作成
        state = HookState("cli-test-sess")
        state.set_prev_topic(1)
        state.increment_nudge_counter()

        # ファイルが存在する
        assert state.get_prev_topic() == 1

        # CLIで clear を実行（HOOK_STATE_DIR環境変数でBASE_DIRをオーバーライド）
        project_root = Path(__file__).resolve().parents[2]
        input_json = json.dumps({"session_id": "cli-test-sess"})
        result = subprocess.run(
            [sys.executable, "hooks/hook_state.py", "clear"],
            input=input_json,
            capture_output=True,
            text=True,
            cwd=str(project_root),
            env={**os.environ, "HOOK_STATE_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

        # CLIで実際にファイルが削除されたことを確認
        assert state.get_prev_topic() is None
        assert state.get_nudge_counter() == 0
