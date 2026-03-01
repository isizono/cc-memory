"""parse_meta_tag.py のユニットテスト"""

import sys
from pathlib import Path

import pytest

# hooksディレクトリをパスに追加
hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(hooks_dir))

from parse_meta_tag import parse_meta_tag, extract_text_from_entry


class TestParseMetaTag:
    """parse_meta_tag関数のテスト"""

    def test_valid_meta_tag(self):
        """正常なメタタグをパースできる"""
        text = '<!-- [meta] subject: claude-code-exterminal-memory (id: 2) | topic: Stopフック実装 (id: 55) -->'
        result = parse_meta_tag(text)
        assert result == {"found": True, "subject_name": "claude-code-exterminal-memory", "subject_id": 2, "topic_name": "Stopフック実装", "topic_id": 55}

    def test_meta_tag_with_surrounding_text(self):
        """前後にテキストがあってもパースできる"""
        text = """これは応答の本文です。

<!-- [meta] subject: テストサブジェクト (id: 10) | topic: テストトピック (id: 100) -->"""
        result = parse_meta_tag(text)
        assert result == {"found": True, "subject_name": "テストサブジェクト", "subject_id": 10, "topic_name": "テストトピック", "topic_id": 100}

    def test_meta_tag_with_japanese(self):
        """日本語のサブジェクト名・トピック名をパースできる"""
        text = '<!-- [meta] subject: 日本語サブジェクト (id: 5) | topic: 日本語トピック (id: 99) -->'
        result = parse_meta_tag(text)
        assert result == {"found": True, "subject_name": "日本語サブジェクト", "subject_id": 5, "topic_name": "日本語トピック", "topic_id": 99}

    def test_no_meta_tag(self):
        """メタタグがない場合はNoneを返す"""
        text = "これはただのテキストです。メタタグはありません。"
        result = parse_meta_tag(text)
        assert result is None

    def test_empty_text(self):
        """空文字列の場合はNoneを返す"""
        result = parse_meta_tag("")
        assert result is None

    def test_malformed_meta_tag(self):
        """不正なフォーマットはNoneを返す"""
        # idがない
        text = '<!-- [meta] subject: test | topic: test -->'
        result = parse_meta_tag(text)
        assert result is None

    def test_large_ids(self):
        """大きなID値もパースできる"""
        text = '<!-- [meta] subject: big (id: 999999) | topic: numbers (id: 888888) -->'
        result = parse_meta_tag(text)
        assert result == {"found": True, "subject_name": "big", "subject_id": 999999, "topic_name": "numbers", "topic_id": 888888}

    def test_name_with_parentheses(self):
        """名前に括弧が含まれていてもパースできる"""
        text = '<!-- [meta] subject: テスト(仮) (id: 2) | topic: 機能追加(v2) (id: 55) -->'
        result = parse_meta_tag(text)
        assert result == {"found": True, "subject_name": "テスト(仮)", "subject_id": 2, "topic_name": "機能追加(v2)", "topic_id": 55}

    def test_old_project_format_not_matched(self):
        """旧フォーマット（project:）はマッチしない"""
        text = '<!-- [meta] project: old-format (id: 1) | topic: some topic (id: 2) -->'
        result = parse_meta_tag(text)
        assert result is None


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
        # tool_useは無視される（この関数ではtextのみ抽出）
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
