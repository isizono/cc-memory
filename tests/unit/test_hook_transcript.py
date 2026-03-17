"""hooks/hook_transcript.py のユニットテスト"""
import json
from pathlib import Path

import pytest

from hooks.hook_transcript import (
    extract_events,
    extract_text_from_entry,
    get_last_assistant_entry,
    get_transcript_info,
    has_context_retrieval_calls,
    has_recent_recording,
    is_user_message,
    parse_meta_tag,
)


# --- ヘルパー ---


def _write_transcript(lines: list[dict], path: Path) -> None:
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def _make_assistant_entry(tool_calls: list[str] | None = None, text: str = "",
                          tool_inputs: list[dict] | None = None) -> dict:
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


# --- parse_meta_tag ---


class TestParseMetaTag:
    """parse_meta_tag関数のテスト"""

    def test_valid_meta_tag_new_format(self):
        """新形式（idなし）のメタタグをパースできる"""
        text = '<!-- [meta] topic: Stopフック実装 -->'
        result = parse_meta_tag(text)
        assert result == {"found": True, "topic_name": "Stopフック実装"}

    def test_meta_tag_with_surrounding_text(self):
        """前後にテキストがあってもパースできる"""
        text = """これは応答の本文です。

<!-- [meta] topic: テストトピック -->"""
        result = parse_meta_tag(text)
        assert result == {"found": True, "topic_name": "テストトピック"}

    def test_meta_tag_with_japanese(self):
        """日本語のトピック名をパースできる"""
        text = '<!-- [meta] topic: 日本語トピック -->'
        result = parse_meta_tag(text)
        assert result == {"found": True, "topic_name": "日本語トピック"}

    def test_no_meta_tag(self):
        """メタタグがない場合はNoneを返す"""
        text = "これはただのテキストです。メタタグはありません。"
        result = parse_meta_tag(text)
        assert result is None

    def test_empty_text(self):
        """空文字列の場合はNoneを返す"""
        result = parse_meta_tag("")
        assert result is None

    def test_name_with_parentheses(self):
        """名前に括弧が含まれていてもパースできる"""
        text = '<!-- [meta] topic: 機能追加(v2) -->'
        result = parse_meta_tag(text)
        assert result == {"found": True, "topic_name": "機能追加(v2)"}

    def test_old_subject_format_not_matched(self):
        """旧フォーマット（subject:付き）はマッチしない"""
        text = '<!-- [meta] subject: old-format | topic: some topic -->'
        result = parse_meta_tag(text)
        assert result is None

    def test_old_project_format_not_matched(self):
        """旧フォーマット（project:）はマッチしない"""
        text = '<!-- [meta] project: old-format | topic: some topic -->'
        result = parse_meta_tag(text)
        assert result is None


# --- extract_text_from_entry ---


class TestExtractTextFromEntry:
    """extract_text_from_entry関数のテスト"""

    def test_string_content(self):
        """contentが文字列の場合"""
        entry = {"message": {"content": "テキスト内容"}}
        result = extract_text_from_entry(entry)
        assert result == "テキスト内容"

    def test_list_content_with_text_blocks(self):
        """contentがtextブロックのリストの場合"""
        entry = {
            "message": {
                "content": [
                    {"type": "text", "text": "最初のテキスト"},
                    {"type": "text", "text": "2番目のテキスト"},
                ]
            }
        }
        result = extract_text_from_entry(entry)
        assert result == "最初のテキスト\n2番目のテキスト"

    def test_list_content_with_mixed_blocks(self):
        """contentにtool_useブロックが混在する場合"""
        entry = {
            "message": {
                "content": [
                    {"type": "text", "text": "テキスト"},
                    {"type": "tool_use", "name": "Read"},
                ]
            }
        }
        result = extract_text_from_entry(entry)
        assert result == "テキスト"

    def test_empty_message(self):
        """messageが空の場合"""
        entry = {"message": {}}
        result = extract_text_from_entry(entry)
        assert result == ""

    def test_no_message(self):
        """messageがない場合"""
        entry = {}
        result = extract_text_from_entry(entry)
        assert result == ""

    def test_list_of_strings(self):
        """contentが文字列のリストの場合"""
        entry = {"message": {"content": ["文字列1", "文字列2"]}}
        result = extract_text_from_entry(entry)
        assert result == "文字列1\n文字列2"


# --- get_last_assistant_entry ---


class TestGetLastAssistantEntry:
    def test_normal_transcript(self, tmp_path):
        """正常なtranscriptから最後のassistantエントリを取得できる"""
        path = tmp_path / "transcript.jsonl"
        lines = [
            _make_user_entry("hi"),
            _make_assistant_entry(text="response 1"),
            _make_user_entry("more"),
            _make_assistant_entry(text="response 2"),
        ]
        _write_transcript(lines, path)
        result = get_last_assistant_entry(str(path))
        assert result is not None
        assert result["message"]["content"][0]["text"] == "response 2"

    def test_no_assistant_entry(self, tmp_path):
        """assistantエントリがない場合はNoneを返す"""
        path = tmp_path / "transcript.jsonl"
        lines = [_make_user_entry("only user")]
        _write_transcript(lines, path)
        assert get_last_assistant_entry(str(path)) is None

    def test_file_not_found(self, tmp_path):
        """ファイルが存在しない場合はNoneを返す"""
        path = tmp_path / "nonexistent.jsonl"
        assert get_last_assistant_entry(str(path)) is None


# --- has_recent_recording ---


class TestHasRecentRecording:
    def test_no_tool_calls(self):
        entries = [_make_assistant_entry(text="hello")]
        assert has_recent_recording(entries) is False

    def test_unrelated_tool_calls(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__search"])]
        assert has_recent_recording(entries) is False

    def test_add_decision_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decisions"])]
        assert has_recent_recording(entries) is True

    def test_add_topic_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_topic"])]
        assert has_recent_recording(entries) is True

    def test_mixed_entries(self):
        entries = [
            _make_assistant_entry(text="thinking..."),
            _make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__search"]),
            _make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decisions"]),
        ]
        assert has_recent_recording(entries) is True

    def test_string_content(self):
        entry = {"type": "assistant", "message": {"content": "just a string"}}
        assert has_recent_recording([entry]) is False


# --- has_context_retrieval_calls ---


class TestHasContextRetrievalCalls:
    def test_no_tool_calls(self):
        entries = [_make_assistant_entry(text="hello")]
        assert has_context_retrieval_calls(entries) is False

    def test_get_topics_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__get_topics"])]
        assert has_context_retrieval_calls(entries) is True

    def test_search_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__search"])]
        assert has_context_retrieval_calls(entries) is True

    def test_get_activities_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__get_activities"])]
        assert has_context_retrieval_calls(entries) is True

    def test_get_by_ids_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__get_by_ids"])]
        assert has_context_retrieval_calls(entries) is True

    def test_recording_tool_not_detected(self):
        """記録ツールはコンテキスト取得とみなさない"""
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decisions"])]
        assert has_context_retrieval_calls(entries) is False


# --- get_transcript_info ---


def _make_user_entry_real(text: str = "hello") -> dict:
    """実際のtranscript形式（type: "user"）でuserエントリを作成する。"""
    return {"type": "user", "message": {"role": "user", "content": text}}


class TestGetTranscriptInfo:
    def test_returns_assistant_entries_and_no_skill(self, tmp_path):
        """通常のtranscript: assistant entriesを返し、skill commandなし"""
        path = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry_real("hi"),
            _make_assistant_entry(text="response"),
            _make_user_entry_real("more"),
            _make_assistant_entry(text="response2"),
        ], path)
        entries, has_skill = get_transcript_info(str(path))
        assert len(entries) == 2
        assert has_skill is False

    def test_detects_skill_command(self, tmp_path):
        """<command-name>を含むuserエントリでskill検出"""
        path = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry_real(
                '<command-message>sync-memory</command-message>\n'
                '<command-name>/claude-code-memory:sync-memory</command-name>'
            ),
            _make_assistant_entry(text="processing skill"),
        ], path)
        entries, has_skill = get_transcript_info(str(path))
        assert len(entries) == 1
        assert has_skill is True

    def test_only_last_user_entry_matters(self, tmp_path):
        """直近のuserエントリのみが判定対象"""
        path = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry_real(
                '<command-name>/sync-memory</command-name>'
            ),
            _make_assistant_entry(text="skill response"),
            _make_user_entry_real("normal message"),
            _make_assistant_entry(text="normal response"),
        ], path)
        entries, has_skill = get_transcript_info(str(path))
        assert len(entries) == 2
        assert has_skill is False

    def test_handles_human_type(self, tmp_path):
        """type: humanのエントリでもskill検出可能"""
        path = tmp_path / "transcript.jsonl"
        _write_transcript([
            {"type": "human", "message": {"content": "<command-name>/test</command-name>"}},
            _make_assistant_entry(text="response"),
        ], path)
        entries, has_skill = get_transcript_info(str(path))
        assert has_skill is True

    def test_handles_list_content(self, tmp_path):
        """contentがリスト形式でもskill検出可能"""
        path = tmp_path / "transcript.jsonl"
        _write_transcript([
            {"type": "user", "message": {"content": [
                {"type": "text", "text": "<command-name>/test</command-name>"},
            ]}},
            _make_assistant_entry(text="response"),
        ], path)
        entries, has_skill = get_transcript_info(str(path))
        assert has_skill is True

    def test_tool_result_does_not_overwrite_skill_detection(self, tmp_path):
        """tool_resultエントリがskill検出を上書きしない"""
        path = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry_real(
                '<command-message>sync-memory</command-message>\n'
                '<command-name>/claude-code-memory:sync-memory</command-name>'
            ),
            _make_assistant_entry(text="calling tools"),
            # tool_result: type="user"だがskill検出を上書きしてはいけない
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_123", "content": "result"},
            ]}},
            _make_assistant_entry(text="more tools"),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_456", "content": "result2"},
            ]}},
            _make_assistant_entry(text="final response"),
        ], path)
        entries, has_skill = get_transcript_info(str(path))
        assert len(entries) == 3
        assert has_skill is True

    def test_real_user_message_after_tool_result_overrides_skill(self, tmp_path):
        """tool_result後の本物のuserメッセージはskill検出を上書きする"""
        path = tmp_path / "transcript.jsonl"
        _write_transcript([
            _make_user_entry_real(
                '<command-name>/sync-memory</command-name>'
            ),
            _make_assistant_entry(text="skill response"),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_123", "content": "result"},
            ]}},
            _make_assistant_entry(text="more response"),
            _make_user_entry_real("normal follow-up message"),
            _make_assistant_entry(text="normal response"),
        ], path)
        entries, has_skill = get_transcript_info(str(path))
        assert has_skill is False

    def test_file_not_found(self, tmp_path):
        """ファイルが存在しない場合"""
        path = tmp_path / "nonexistent.jsonl"
        entries, has_skill = get_transcript_info(str(path))
        assert entries == []
        assert has_skill is False

    def test_empty_file(self, tmp_path):
        """空ファイルの場合"""
        path = tmp_path / "transcript.jsonl"
        path.write_text("")
        entries, has_skill = get_transcript_info(str(path))
        assert entries == []
        assert has_skill is False


# --- is_user_message ---


class TestIsUserMessage:
    def test_string_content_is_user_message(self):
        entry = {"type": "user", "message": {"content": "hello"}}
        assert is_user_message(entry) is True

    def test_list_content_without_tool_result_is_user_message(self):
        entry = {"type": "user", "message": {"content": [
            {"type": "text", "text": "hello"},
        ]}}
        assert is_user_message(entry) is True

    def test_tool_result_is_not_user_message(self):
        entry = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_123", "content": "result"},
        ]}}
        assert is_user_message(entry) is False

    def test_assistant_is_not_user_message(self):
        entry = {"type": "assistant", "message": {"content": "hello"}}
        assert is_user_message(entry) is False

    def test_is_meta_entry_is_not_user_message(self):
        """isMeta=trueのエントリ（スキル内容注入等）はUser Messageではない"""
        entry = {
            "type": "user",
            "isMeta": True,
            "message": {"content": [
                {"type": "text", "text": "Base directory for this skill: ..."},
            ]},
        }
        assert is_user_message(entry) is False

    def test_is_meta_false_is_user_message(self):
        """isMeta=falseは通常のUser Message"""
        entry = {
            "type": "user",
            "isMeta": False,
            "message": {"content": "hello"},
        }
        assert is_user_message(entry) is True


# --- extract_events: isMeta handling ---


class TestExtractEventsIsMeta:
    def test_is_meta_does_not_advance_turn(self):
        """isMeta=trueのエントリはturnを進めない"""
        entries = [
            # ユーザーのスキル呼び出し
            {"type": "user", "message": {"content":
                "<command-name>/check-in</command-name>"}},
            # スキル内容注入（isMeta=true）
            {"type": "user", "isMeta": True, "message": {"content": [
                {"type": "text", "text": "Base directory for this skill..."},
            ]}},
            # アシスタント応答
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "response"},
            ]}},
        ]
        events, current_turn = extract_events(entries, 0)
        # turn 1のみ（isMeta=trueでturn 2にならない）
        assert current_turn == 1
        skill_events = [e for e in events if e["e"] == "skill"]
        assert len(skill_events) == 1
        assert skill_events[0]["turn"] == 1
