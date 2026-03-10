"""add_knowledge MCPツールの統合テスト"""
import os
import tempfile
import pytest
from pathlib import Path
from src.services.knowledge_service import add_knowledge


@pytest.fixture
def temp_knowledge_root():
    """テスト用の一時的なKNOWLEDGE_ROOTを作成する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["KNOWLEDGE_ROOT"] = tmpdir
        yield Path(tmpdir)
        # クリーンアップ
        if "KNOWLEDGE_ROOT" in os.environ:
            del os.environ["KNOWLEDGE_ROOT"]


# ========================================
# MCPツール経由でのナレッジ保存テスト
# ========================================


def test_add_knowledge_via_mcp_tool(temp_knowledge_root):
    """MCPツール経由でナレッジを保存できる"""
    result = add_knowledge(
        title="Claude Code hooks調査",
        content="# 調査結果\n\nhooksの仕組みについて...",
        tags=["claude-code", "hooks"],
        category="references",
    )

    assert "error" not in result
    assert "file_path" in result
    assert result["title"] == "Claude Code hooks調査"
    assert result["category"] == "references"
    assert result["tags"] == ["claude-code", "hooks"]


def test_add_knowledge_file_actually_created(temp_knowledge_root):
    """実際にファイルが作成されることを確認"""
    result = add_knowledge(
        title="テストファイル",
        content="テスト内容",
        tags=["test"],
        category="references",
    )

    filepath = Path(result["file_path"])
    assert filepath.exists()
    assert filepath.is_file()


def test_add_knowledge_file_content_format(temp_knowledge_root):
    """ファイル内容のフォーマットが正しいことを確認"""
    result = add_knowledge(
        title="フォーマットテスト",
        content="# 見出し\n\n本文です。",
        tags=["tag1", "tag2"],
        category="references",
    )

    filepath = Path(result["file_path"])
    content = filepath.read_text(encoding="utf-8")

    # YAMLフロントマターの確認
    assert content.startswith("---")
    assert "tags:" in content
    assert "- tag1" in content
    assert "- tag2" in content
    assert "created_at:" in content
    # フロントマターが閉じていることを確認
    assert content.count("---") >= 2

    # 本文の確認
    assert "# 見出し" in content
    assert "本文です。" in content


def test_add_knowledge_references_directory(temp_knowledge_root):
    """referencesカテゴリのファイルが正しいディレクトリに保存される"""
    result = add_knowledge(
        title="外部情報テスト",
        content="content",
        tags=[],
        category="references",
    )

    filepath = Path(result["file_path"])
    assert filepath.parent.name == "references"
    assert filepath.parent == temp_knowledge_root / "references"


def test_add_knowledge_facts_directory(temp_knowledge_root):
    """factsカテゴリのファイルが正しいディレクトリに保存される"""
    result = add_knowledge(
        title="コードベーステスト",
        content="content",
        tags=[],
        category="facts",
    )

    filepath = Path(result["file_path"])
    assert filepath.parent.name == "facts"
    assert filepath.parent == temp_knowledge_root / "facts"


def test_add_knowledge_japanese_title(temp_knowledge_root):
    """日本語タイトルでファイルが作成される"""
    result = add_knowledge(
        title="日本語のナレッジタイトル",
        content="content",
        tags=[],
        category="references",
    )

    filepath = Path(result["file_path"])
    assert "日本語のナレッジタイトル.md" in str(filepath)


def test_add_knowledge_collision_handling(temp_knowledge_root):
    """同名ナレッジの衝突処理が正しく動作する"""
    # 1回目
    result1 = add_knowledge(
        title="重複タイトル",
        content="1回目の内容",
        tags=[],
        category="references",
    )

    # 2回目（同名）
    result2 = add_knowledge(
        title="重複タイトル",
        content="2回目の内容",
        tags=[],
        category="references",
    )

    # 両方成功し、別のファイルパス
    assert "error" not in result1
    assert "error" not in result2
    assert result1["file_path"] != result2["file_path"]

    # 2回目は連番付き
    assert "重複タイトル.md" in result1["file_path"]
    assert "重複タイトル_1.md" in result2["file_path"]

    # 両方のファイルが存在
    assert Path(result1["file_path"]).exists()
    assert Path(result2["file_path"]).exists()


def test_add_knowledge_invalid_category_error(temp_knowledge_root):
    """無効なカテゴリでエラーが返る"""
    result = add_knowledge(
        title="test",
        content="content",
        tags=[],
        category="invalid_category",
    )

    assert "error" in result
    assert result["error"]["code"] == "INVALID_CATEGORY"


def test_add_knowledge_empty_tags(temp_knowledge_root):
    """空のタグリストでも保存できる"""
    result = add_knowledge(
        title="タグなしナレッジ",
        content="content",
        tags=[],
        category="references",
    )

    assert "error" not in result
    assert result["tags"] == []

    # ファイル内容を確認
    filepath = Path(result["file_path"])
    content = filepath.read_text(encoding="utf-8")
    assert "tags: []" in content


def test_add_knowledge_multiple_tags(temp_knowledge_root):
    """複数タグで保存できる"""
    result = add_knowledge(
        title="複数タグナレッジ",
        content="content",
        tags=["tag1", "tag2", "tag3", "unicode-tag"],
        category="references",
    )

    assert "error" not in result
    assert result["tags"] == ["tag1", "tag2", "tag3", "unicode-tag"]

    # ファイル内容を確認
    filepath = Path(result["file_path"])
    content = filepath.read_text(encoding="utf-8")
    assert "- tag1" in content
    assert "- tag2" in content
    assert "- tag3" in content
    assert "- unicode-tag" in content


def test_add_knowledge_markdown_content(temp_knowledge_root):
    """マークダウン形式のコンテンツが保存できる"""
    markdown_content = """# 見出し1

## 見出し2

- リスト1
- リスト2

```python
def hello():
    print("Hello, World!")
```

> 引用文

**太字** と *イタリック*
"""
    result = add_knowledge(
        title="マークダウンテスト",
        content=markdown_content,
        tags=["markdown"],
        category="references",
    )

    assert "error" not in result

    filepath = Path(result["file_path"])
    content = filepath.read_text(encoding="utf-8")

    # マークダウン要素が保持されていることを確認
    assert "# 見出し1" in content
    assert "## 見出し2" in content
    assert "- リスト1" in content
    assert "```python" in content
    assert "> 引用文" in content
