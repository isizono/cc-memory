"""hooks/stop_hook.py の E2E テスト

subprocess.run で stop_hook.py を呼び出し、stdin→stdout の入出力をテスト。
テスト用に tmpdir の state を使う（HOOK_STATE_DIR 環境変数）。
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


META_TAG = '<!-- [meta] topic: test-topic -->'
META_TAG_TOPIC_B = '<!-- [meta] topic: another-topic -->'
CONTEXT_RETRIEVAL_ENTRY = _make_assistant_entry(
    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__get_topics"],
)


def _run_stop_hook(
    transcript_path: str,
    session_id: str,
    env_override: dict | None = None,
    last_assistant_message: str = "",
    return_stderr: bool = False,
) -> dict | tuple[dict, str]:
    """stop_hook.py を subprocess で実行し、出力 JSON を返す

    Args:
        return_stderr: True の場合、(json_result, stderr_text) のタプルを返す
    """
    input_data = json.dumps({
        "transcript_path": transcript_path,
        "session_id": session_id,
        "last_assistant_message": last_assistant_message,
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
    parsed = json.loads(stdout)

    if return_stderr:
        return parsed, result.stderr
    return parsed


# --- Fixtures ---


@pytest.fixture
def env_setup(tmp_path):
    """テスト用環境をセットアップし、env_override dict を返す"""
    state_dir = str(tmp_path / "state")
    os.makedirs(state_dir, exist_ok=True)

    env_override = {
        "HOOK_STATE_DIR": state_dir,
    }

    yield {
        "env_override": env_override,
        "tmp_path": tmp_path,
        "state_dir": state_dir,
    }


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


class TestMetaTagApproves:
    """2. メタタグあり + コンテキスト取得済み → approve"""

    def test_meta_tag_with_context_retrieval_approves(self, env_setup):
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                CONTEXT_RETRIEVAL_ENTRY,
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )
        assert result["decision"] == "approve"


class TestTopicChangeNoRecord:
    """3. トピック変更 + 記録なし → block"""

    def test_topic_change_without_record_blocks(self, env_setup):
        state_dir = Path(env_setup["state_dir"])

        # prev_topic をトピック名で設定
        prev_file = state_dir / "prev_topic_test-session"
        prev_file.write_text("test-topic")

        # context_retrieved フラグを設定（既にコンテキスト取得済み）
        context_file = state_dir / "context_retrieved_test-session"
        context_file.write_text("1")

        # 別トピックに変更するtranscript（記録なし）
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG_TOPIC_B}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG_TOPIC_B}",
        )
        assert result["decision"] == "block"
        assert "トピックが変わりました" in result["reason"]


class TestTopicChangeWithRecord:
    """4. トピック変更 + 記録あり → approve"""

    def test_topic_change_with_record_approves(self, env_setup):
        state_dir = Path(env_setup["state_dir"])

        # prev_topic をトピック名で設定
        prev_file = state_dir / "prev_topic_test-session"
        prev_file.write_text("test-topic")

        # context_retrieved フラグを設定
        context_file = state_dir / "context_retrieved_test-session"
        context_file.write_text("1")

        # 別トピックに変更するtranscript（add_decision記録あり）
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(
                    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decision"],
                ),
                _make_user_entry("continue"),
                _make_assistant_entry(text=f"{META_TAG_TOPIC_B}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG_TOPIC_B}",
        )
        assert result["decision"] == "approve"


class TestBlockLimitForceApprove:
    """5. ブロック上限2回 → force approve"""

    def test_block_limit_force_approves(self, env_setup):
        state_dir = Path(env_setup["state_dir"])

        # block_count を 2 にセット（上限到達）
        block_file = state_dir / "block_count_test-session"
        block_file.write_text("2")

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
    """6. 例外発生時 → approve（フェイルオープン）"""

    def test_exception_causes_approve(self, env_setup):
        # state_dir をファイルにして HookState.__init__ の mkdir を失敗させる
        state_as_file = env_setup["tmp_path"] / "state_as_file"
        state_as_file.write_text("not a directory")

        env_override = {
            "HOOK_STATE_DIR": str(state_as_file),
        }

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_override,
            last_assistant_message=f"response\n{META_TAG}",
        )
        assert result["decision"] == "approve"
        assert "error" in result.get("reason", "").lower()


class TestNudgeCounter:
    """nudgeカウンターの動作確認"""

    def test_nudge_pending_set_on_interval_without_recording(self, env_setup):
        """インターバル到達で記録なし → nudge_pending が設定される"""
        state_dir = Path(env_setup["state_dir"])

        # nudge_counter を 1 にセット（次が2の倍数）
        counter_file = state_dir / "nudge_counter_test-session"
        counter_file.write_text("1")

        # context_retrieved フラグを設定
        context_file = state_dir / "context_retrieved_test-session"
        context_file.write_text("1")

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )
        assert result["decision"] == "approve"

        # nudge_pending が設定されている
        pending_file = state_dir / "nudge_pending_test-session"
        assert pending_file.exists()

    def test_nudge_counter_resets_on_recent_recording(self, env_setup):
        """インターバル到達で記録あり → nudge_counter がリセットされる"""
        state_dir = Path(env_setup["state_dir"])

        # nudge_counter を 1 にセット
        counter_file = state_dir / "nudge_counter_test-session"
        counter_file.write_text("1")

        # context_retrieved フラグを設定
        context_file = state_dir / "context_retrieved_test-session"
        context_file.write_text("1")

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(
                    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decision"],
                    text=f"{META_TAG}\nrecorded",
                ),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"recorded\n{META_TAG}",
        )
        assert result["decision"] == "approve"

        # nudge_counter がリセットされている（ファイル削除）
        assert not counter_file.exists()

    def test_nudge_counter_resets_on_add_log(self, env_setup):
        """インターバル到達でadd_log記録あり → nudge_counter がリセットされる"""
        state_dir = Path(env_setup["state_dir"])

        counter_file = state_dir / "nudge_counter_test-session"
        counter_file.write_text("1")

        # context_retrieved フラグを設定
        context_file = state_dir / "context_retrieved_test-session"
        context_file.write_text("1")

        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(
                    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_log"],
                    text=f"logged\n{META_TAG}",
                ),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"logged\n{META_TAG}",
        )
        assert result["decision"] == "approve"

        # add_logでもnudge_counterがリセットされる
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
                CONTEXT_RETRIEVAL_ENTRY,
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )

        prev_file = state_dir / "prev_topic_test-session"
        assert prev_file.exists()
        assert prev_file.read_text().strip() == "test-topic"

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
                CONTEXT_RETRIEVAL_ENTRY,
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )

        # block_count がリセットされている
        assert not block_file.exists()


class TestSplitEntries:
    """エントリ分割パターン: text+meta と tool_use が別エントリ"""

    def test_split_entry_with_last_assistant_message(self, env_setup):
        """分割エントリでもlast_assistant_messageで検出できる"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        # text+meta と tool_use を別エントリに分割
        _write_transcript(
            [
                _make_user_entry("hi"),
                CONTEXT_RETRIEVAL_ENTRY,
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),  # textのみ
                _make_assistant_entry(tool_calls=["Bash"]),  # tool_useのみ
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"{META_TAG}\nresponse",
        )
        assert result["decision"] == "approve"

    def test_split_entry_fallback_to_transcript(self, env_setup):
        """last_assistant_messageなしでもtranscriptフォールバックで検出"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                CONTEXT_RETRIEVAL_ENTRY,
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
                _make_assistant_entry(tool_calls=["Bash"]),
            ],
            transcript,
        )

        # last_assistant_message なし → transcriptフォールバック
        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
        )
        assert result["decision"] == "approve"


class TestLastAssistantMessage:
    """last_assistant_message あり/なしの両パターン"""

    def test_meta_tag_via_last_assistant_message(self, env_setup):
        """last_assistant_messageからメタタグを検出（レースコンディション模擬）"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        # transcriptにはメタタグなし（レースコンディション模擬）だが
        # コンテキスト取得ツールの呼び出しは含める
        _write_transcript(
            [
                _make_user_entry("hi"),
                CONTEXT_RETRIEVAL_ENTRY,
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"{META_TAG}\nresponse",
        )
        assert result["decision"] == "approve"

    def test_no_last_assistant_message_falls_back_to_transcript(self, env_setup):
        """last_assistant_messageなし → transcriptから検出"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                CONTEXT_RETRIEVAL_ENTRY,
                _make_assistant_entry(text=f"response\n{META_TAG}"),
            ],
            transcript,
        )

        # last_assistant_message なし
        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
        )
        assert result["decision"] == "approve"


class TestContextRetrievalCheck:
    """コンテキスト取得ツール呼び出しチェック"""

    def test_no_context_retrieval_blocks(self, env_setup):
        """コンテキスト取得ツール未呼出 + メタタグあり → block"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )
        assert result["decision"] == "block"
        assert "コンテキスト" in result["reason"]

    def test_get_topics_call_approves(self, env_setup):
        """get_topics呼出済み + メタタグあり → approve"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                CONTEXT_RETRIEVAL_ENTRY,
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )
        assert result["decision"] == "approve"

    def test_search_call_approves(self, env_setup):
        """search呼出済み + メタタグあり → approve"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(
                    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__search"],
                ),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )
        assert result["decision"] == "approve"

    def test_get_by_ids_call_approves(self, env_setup):
        """get_by_ids呼出済み + メタタグあり → approve"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(
                    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__get_by_ids"],
                ),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )
        assert result["decision"] == "approve"

    def test_add_topic_does_not_count_as_retrieval(self, env_setup):
        """add_topicは記録ツールでありコンテキスト取得とみなさない → block"""
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(
                    tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_topic"],
                ),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )
        assert result["decision"] == "block"
        assert "コンテキスト" in result["reason"]

    def test_context_retrieval_flag_persists(self, env_setup):
        """一度コンテキスト取得したらフラグで記憶される"""
        state_dir = Path(env_setup["state_dir"])

        # context_retrieved フラグを事前設定
        context_file = state_dir / "context_retrieved_test-session"
        context_file.write_text("1")

        # transcriptにはコンテキスト取得ツールなし
        transcript = env_setup["tmp_path"] / "transcript.jsonl"
        _write_transcript(
            [
                _make_user_entry("hi"),
                _make_assistant_entry(text=f"{META_TAG}\nresponse"),
            ],
            transcript,
        )

        result = _run_stop_hook(
            str(transcript), "test-session", env_setup["env_override"],
            last_assistant_message=f"response\n{META_TAG}",
        )
        # フラグがあるのでblockされない
        assert result["decision"] == "approve"
