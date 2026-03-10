"""hooks/hook_transcript.py のユニットテスト"""
import json
from pathlib import Path

import pytest

from hooks.hook_transcript import (
    extract_text_from_entry,
    get_assistant_entries,
    get_last_assistant_entry,
    has_context_retrieval_calls,
    has_recent_recording,
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


# --- get_assistant_entries ---


class TestGetAssistantEntries:
    def test_get_all(self, tmp_path):
        """全件取得"""
        path = tmp_path / "transcript.jsonl"
        lines = [
            _make_user_entry(),
            _make_assistant_entry(text="msg0"),
            _make_user_entry(),
            _make_assistant_entry(text="msg1"),
            _make_user_entry(),
            _make_assistant_entry(text="msg2"),
        ]
        _write_transcript(lines, path)
        result = get_assistant_entries(str(path))
        assert len(result) == 3

    def test_get_last_n(self, tmp_path):
        """last_n指定"""
        path = tmp_path / "transcript.jsonl"
        entries = [_make_assistant_entry(text=f"msg{i}") for i in range(5)]
        lines = []
        for e in entries:
            lines.append(_make_user_entry())
            lines.append(e)
        _write_transcript(lines, path)

        result = get_assistant_entries(str(path), last_n=3)
        assert len(result) == 3
        assert result[0]["message"]["content"][0]["text"] == "msg2"
        assert result[1]["message"]["content"][0]["text"] == "msg3"
        assert result[2]["message"]["content"][0]["text"] == "msg4"

    def test_file_not_found(self, tmp_path):
        """ファイルが存在しない場合は空リストを返す"""
        path = tmp_path / "nonexistent.jsonl"
        assert get_assistant_entries(str(path)) == []

    def test_empty_file(self, tmp_path):
        """空ファイルの場合は空リストを返す"""
        path = tmp_path / "transcript.jsonl"
        path.write_text("")
        assert get_assistant_entries(str(path)) == []

    def test_no_assistant_entries(self, tmp_path):
        """ユーザーエントリのみの場合は空リストを返す"""
        path = tmp_path / "transcript.jsonl"
        _write_transcript([_make_user_entry("only user entries")], path)
        assert get_assistant_entries(str(path)) == []


# --- has_recent_recording ---


class TestHasRecentRecording:
    def test_no_tool_calls(self):
        entries = [_make_assistant_entry(text="hello")]
        assert has_recent_recording(entries) is False

    def test_unrelated_tool_calls(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__search"])]
        assert has_recent_recording(entries) is False

    def test_add_decision_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decision"])]
        assert has_recent_recording(entries) is True

    def test_add_topic_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_topic"])]
        assert has_recent_recording(entries) is True

    def test_mixed_entries(self):
        entries = [
            _make_assistant_entry(text="thinking..."),
            _make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__search"]),
            _make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decision"]),
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

    def test_get_by_ids_detected(self):
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__get_by_ids"])]
        assert has_context_retrieval_calls(entries) is True

    def test_recording_tool_not_detected(self):
        """記録ツールはコンテキスト取得とみなさない"""
        entries = [_make_assistant_entry(tool_calls=["mcp__plugin_claude-code-memory_cc-memory__add_decision"])]
        assert has_context_retrieval_calls(entries) is False
