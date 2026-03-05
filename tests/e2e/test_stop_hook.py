"""hooks/stop_hook.py の E2E テスト

subprocess.run で stop_hook.py を呼び出し、stdin→stdout の入出力をテスト。
テスト用に tmpdir の DB と state を使う（DISCUSSION_DB_PATH, HOOK_STATE_DIR 環境変数）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# --- ヘルパー ---


def _write_transcript(lines: list[dict], path: Path) -> None:
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def _make_assistant_entry(
    tool_calls: list[str] | None = None,
    text: str = "",
    tool_inputs: list[dict] | None = None,
) -> dict:
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool_calls:
        for i, tool in enumerate(tool_calls):
            inp = tool_inputs[i] if tool_inputs and i < len(tool_inputs) else {}
            content.append({"type": "tool_use", "name": tool, "input": inp})
    return {"type": "assistant", "message": {"content": content}}


def _make_user_entry(text: str = "hello") -> dict:
    return {"type": "human", "message": {"content": [{"type": "text", "text": text}]}}


META_TAG = '<!-- [meta] subject: test-subject (id: 1) | topic: test-topic (id: 100) -->'
META_TAG_TOPIC_200 = '<!-- [meta] subject: test-subject (id: 1) | topic: another-topic (id: 200) -->'
META_TAG_WRONG_NAME = '<!-- [meta] subject: test-subject (id: 1) | topic: wrong-name (id: 100) -->'
META_TAG_NONEXISTENT = '<!-- [meta] subject: test-subject (id: 1) | topic: ghost (id: 99999) -->'


def _run_stop_hook(
    transcript_path: str,
    session_id: str,
    env_override: dict | None = None,
) -> dict:
    """stop_hook.py を subprocess で実行し、出力 JSON を返す"""
    input_data = json.dumps({
        "transcript_path": transcript_path,
        "session_id": session_id,
    })

    env = {**os.environ}
    if env_override:
        env.update(env_override)

    result = subprocess.run(
        [sys.executable, "hooks/stop_hook.py"],
        input=input_data,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )

    # 標準出力からJSONをパース
    stdout = result.stdout.strip()
    assert stdout, f"stop_hook.py produced no output. stderr: {result.stderr}"
    return json.loads(stdout)


# --- Fixtures ---


@pytest.fixture
def env_setup(tmp_path):
    """テスト用環境をセットアップし、env_override dict を返す"""
    from src.db import get_connection, init_database

    db_path = str(tmp_path / "test.db")
    state_dir = str(tmp_path / "state")
    os.makedirs(state_dir, exist_ok=True)

    # DB初期化
    old_db = os.environ.get("DISCUSSION_DB_PATH")
    os.environ["DISCUSSION_DB_PATH"] = db_path
    init_database()

    # テスト用トピックを追加
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT id FROM subjects WHERE name = 'first_subject'")
        row = cursor.fetchone()
        subject_id = row[0] if row else 1

        conn.execute(
            "INSERT INTO discussion_topics (id, subject_id, title, description) "
            "VALUES (100, ?, 'test-topic', 'Description')",
            (subject_id,),
        )
        conn.execute(
            "INSERT INTO discussion_topics (id, subject_id, title, description) "
            "VALUES (200, ?, 'another-topic', 'Description')",
            (subject_id,),
        )
        conn.commit()
    finally:
        conn.close()

    env_override = {
        "DISCUSSION_DB_PATH": db_path,
        "HOOK_STATE_DIR": state_dir,
    }

    yield {
        "env_override": env_override,
        "tmp_path": tmp_path,
        "state_dir": state_dir,
        "db_path": db_path,
    }

    # クリーンアップ
    if old_db is not None:
        os.environ["DISCUSSION_DB_PATH"] = old_db
    elif "DISCUSSION_DB_PATH" in os.environ:
        del os.environ["DISCUSSION_DB_PATH"]


# --- テストケース ---


class TestNoMetaTag:
    """1. メタタグなし → block"""

    def test_no_meta_tag_blocks(self, env_setup):
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text="response without meta tag"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "block"
        assert "メタタグ" in result["reason"]


class TestMetaTagWithExistingTopic:
    """2. メタタグあり + トピック存在 → approve"""

    def test_meta_tag_with_existing_topic_approves(self, env_setup):
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "approve"


class TestTopicNotExists:
    """3. トピック不存在 → block"""

    def test_nonexistent_topic_blocks(self, env_setup):
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG_NONEXISTENT}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "block"
        assert "topic_id=99999" in result["reason"]
        assert "存在しません" in result["reason"]


class TestTopicNameMismatch:
    """4. トピック名不一致 → approve（nudgeフラグ確認）"""

    def test_name_mismatch_approves_with_nudge(self, env_setup):
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG_WRONG_NAME}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "approve"

        # nudge_topic_name ファイルが作られている
        state_dir = Path(env_setup["state_dir"])
        nudge_file = state_dir / "nudge_topic_name_test-session"
        assert nudge_file.exists()

        nudge_data = json.loads(nudge_file.read_text())
        assert nudge_data["topic_id"] == 100
        assert nudge_data["actual_name"] == "test-topic"


class TestTopicChangeNoRecord:
    """5. トピック変更 + 記録なし → block"""

    def test_topic_change_without_record_blocks(self, env_setup):
        state_dir = Path(env_setup["state_dir"])

        # prev_topic を 100 にセット（first_topic=1 ではない）
        prev_file = state_dir / "prev_topic_test-session"
        prev_file.write_text("100")

        # トピック200に変更するtranscript（100への記録なし）
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG_TOPIC_200}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "block"
        assert "id=100" in result["reason"]
        assert "記録してから" in result["reason"]


class TestTopicChangeWithRecord:
    """6. トピック変更 + 記録あり → approve"""

    def test_topic_change_with_record_approves(self, env_setup):
        state_dir = Path(env_setup["state_dir"])

        # prev_topic を 100 にセット
        prev_file = state_dir / "prev_topic_test-session"
        prev_file.write_text("100")

        # トピック200に変更するtranscript（100へのadd_decision記録あり）
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(
                    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decision"],
                    tool_inputs=[{"topic_id": 100}],
                ),
                _make_user_entry("continue"),
                _make_assistant_entry(text=f"{META_TAG_TOPIC_200}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "approve"


class TestBlockLimitForceApprove:
    """7. ブロック上限3回 → force approve"""

    def test_block_limit_force_approves(self, env_setup):
        state_dir = Path(env_setup["state_dir"])

        # block_count を 3 にセット（上限到達）
        block_file = state_dir / "block_count_test-session"
        block_file.write_text("3")

        # メタタグなしtranscript（通常ならblock）
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text="no meta tag here"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "approve"
        assert "ブロック上限" in result["reason"]

        # block_count がリセットされている
        assert not block_file.exists()


class TestExceptionFailOpen:
    """8. 例外発生時 → approve（フェイルオープン）"""

    def test_exception_causes_approve(self, env_setup):
        # 存在しないtranscriptパスを渡す（メタタグなし → block になるが、
        # それは正常動作。真の例外テストには不正なDBパスを使う）
        env_override = {
            **env_setup["env_override"],
            "DISCUSSION_DB_PATH": "/nonexistent/path/to/db.sqlite",
        }

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                # DBが壊れていてもメタタグなしでblockされるので、メタタグありにする
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_override
        )
        # DB接続エラーが発生しても approve
        assert result["decision"] == "approve"
        assert "error" in result.get("reason", "").lower()


class TestFirstTopicSkip:
    """first_topic(id=1)からの移動はblockしない"""

    def test_first_topic_skip(self, env_setup):
        state_dir = Path(env_setup["state_dir"])

        # prev_topic を 1（first_topic）にセット
        prev_file = state_dir / "prev_topic_test-session"
        prev_file.write_text("1")

        # トピック100に変更するtranscript（1への記録なし）
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        # first_topicからの移動はスキップ → approve
        assert result["decision"] == "approve"


class TestNudgeCounter:
    """nudgeカウンターの動作確認"""

    def test_nudge_pending_set_on_3rd_turn_without_recording(self, env_setup):
        """3ターン目で記録なし → nudge_pending が設定される"""
        state_dir = Path(env_setup["state_dir"])

        # nudge_counter を 2 にセット（次が3の倍数）
        counter_file = state_dir / "nudge_counter_test-session"
        counter_file.write_text("2")

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "approve"

        # nudge_pending が設定されている
        pending_file = state_dir / "nudge_pending_test-session"
        assert pending_file.exists()

    def test_nudge_counter_resets_on_recent_recording(self, env_setup):
        """3ターン目で記録あり → nudge_counter がリセットされる"""
        state_dir = Path(env_setup["state_dir"])

        # nudge_counter を 2 にセット
        counter_file = state_dir / "nudge_counter_test-session"
        counter_file.write_text("2")

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(
                    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decision"],
                    tool_inputs=[{"topic_id": 100}],
                    text=f"{META_TAG}\nrecorded",
                ),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"]
        )
        assert result["decision"] == "approve"

        # nudge_counter がリセットされている（ファイル削除）
        assert not counter_file.exists()


class TestStateUpdatedOnApprove:
    """approve時の状態更新"""

    def test_prev_topic_updated(self, env_setup):
        """approve後に prev_topic が更新される"""
        state_dir = Path(env_setup["state_dir"])

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        _run_stop_hook(str(transcript), "test-session", env_setup["env_override"])

        prev_file = state_dir / "prev_topic_test-session"
        assert prev_file.exists()
        assert prev_file.read_text().strip() == "100"

    def test_block_count_reset_on_approve(self, env_setup):
        """approve後に block_count がリセットされる"""
        state_dir = Path(env_setup["state_dir"])

        # block_count を 1 にセット
        block_file = state_dir / "block_count_test-session"
        block_file.write_text("1")

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        _run_stop_hook(str(transcript), "test-session", env_setup["env_override"])

        # block_count がリセットされている
        assert not block_file.exists()
