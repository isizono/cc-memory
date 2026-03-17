"""MCPツールのテスト（subjects廃止後）"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.topic_service import add_topic
from src.services.activity_service import add_activity


@pytest.fixture
def temp_db():
    """テスト用の一時的なデータベースを作成する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["DISCUSSION_DB_PATH"] = db_path
        init_database()
        yield db_path
        # クリーンアップ
        if "DISCUSSION_DB_PATH" in os.environ:
            del os.environ["DISCUSSION_DB_PATH"]


def test_add_topic_with_tags(temp_db):
    """タグ付きでトピックの追加が成功する"""
    result = add_topic(
        title="test-topic",
        description="テストトピック",
        tags=["domain:test"],
    )

    assert "error" not in result
    assert result["topic_id"] > 0


def test_add_topic_tags_required(temp_db):
    """タグなしでトピック追加するとTAGS_REQUIREDエラーが返る"""
    result = add_topic(
        title="test-topic",
        description="テストトピック",
        tags=[],
    )

    assert "error" in result
    assert result["error"]["code"] == "TAGS_REQUIRED"


def test_add_activity_with_tags(temp_db):
    """タグ付きでアクティビティの追加が成功する"""
    result = add_activity(
        title="test-activity",
        description="テストアクティビティ",
        tags=["domain:test"],
        check_in=False,
    )

    assert "error" not in result
    assert result["activity_id"] > 0


def test_add_activity_tags_required(temp_db):
    """タグなしでアクティビティ追加するとTAGS_REQUIREDエラーが返る"""
    result = add_activity(
        title="test-activity",
        description="テストアクティビティ",
        tags=[],
    )

    assert "error" in result
    assert result["error"]["code"] == "TAGS_REQUIRED"
