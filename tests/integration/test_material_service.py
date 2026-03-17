"""資材サービスの統合テスト"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.activity_service import add_activity
from src.services.material_service import add_material, get_material, update_material
from src.services.search_service import get_by_id, get_by_ids


DEFAULT_TAGS = ["domain:test"]


@pytest.fixture
def temp_db():
    """テスト用の一時的なデータベースを作成する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["DISCUSSION_DB_PATH"] = db_path
        init_database()
        yield db_path
        if "DISCUSSION_DB_PATH" in os.environ:
            del os.environ["DISCUSSION_DB_PATH"]


@pytest.fixture
def activity_id(temp_db):
    """テスト用アクティビティを作成してIDを返すフィクスチャ"""
    result = add_activity(
        title="Test Activity",
        description="Activity for material tests",
        tags=DEFAULT_TAGS,
        check_in=False,
    )
    return result["activity_id"]


class TestAddMaterial:
    """add_materialの統合テスト"""

    def test_add_material_success(self, temp_db):
        """資材の追加が成功する"""
        result = add_material(
            title="Test Material",
            content="# Test Content\n\nThis is a test material.",
            tags=["domain:test", "design"],
        )

        assert "error" not in result
        assert result["material_id"] > 0

    def test_add_material_with_related(self, activity_id):
        """relatedを指定してリレーション付きで資材を作成できる"""
        result = add_material(
            title="Related Material",
            content="Content",
            tags=["domain:test"],
            related=[{"type": "activity", "ids": [activity_id]}],
        )

        assert "error" not in result
        assert result["material_id"] > 0

    def test_add_material_empty_title(self, temp_db):
        """空のtitleでVALIDATION_ERRORになる"""
        result = add_material(
            title="",
            content="Content",
            tags=["domain:test"],
        )

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "title" in result["error"]["message"]

    def test_add_material_whitespace_title(self, temp_db):
        """空白のみのtitleでVALIDATION_ERRORになる"""
        result = add_material(
            title="   ",
            content="Content",
            tags=["domain:test"],
        )

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_add_material_empty_content(self, temp_db):
        """空のcontentでVALIDATION_ERRORになる"""
        result = add_material(
            title="Title",
            content="",
            tags=["domain:test"],
        )

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "content" in result["error"]["message"]

    def test_add_material_whitespace_content(self, temp_db):
        """空白のみのcontentでVALIDATION_ERRORになる"""
        result = add_material(
            title="Title",
            content="   ",
            tags=["domain:test"],
        )

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_add_material_no_tags(self, temp_db):
        """タグなしでTAGS_REQUIREDエラーになる"""
        result = add_material(
            title="Title",
            content="Content",
            tags=[],
        )

        assert "error" in result
        assert result["error"]["code"] == "TAGS_REQUIRED"

    def test_add_multiple_materials(self, temp_db):
        """複数の資材を追加できる"""
        result1 = add_material(
            title="Material 1",
            content="Content 1",
            tags=["domain:test"],
        )
        result2 = add_material(
            title="Material 2",
            content="Content 2",
            tags=["domain:test"],
        )

        assert "error" not in result1
        assert "error" not in result2
        assert result1["material_id"] != result2["material_id"]


class TestGetMaterial:
    """get_materialの統合テスト"""

    def test_get_material_success(self, temp_db):
        """資材の全文取得が成功する"""
        created = add_material(
            title="Get Test",
            content="Full content here",
            tags=["domain:test", "search"],
        )
        material_id = created["material_id"]

        result = get_material(material_id)

        assert "error" not in result
        assert result["material_id"] == material_id
        assert result["title"] == "Get Test"
        assert result["content"] == "Full content here"
        assert "tags" in result
        assert "domain:test" in result["tags"]
        assert "search" in result["tags"]
        assert "created_at" in result
        # activity_idが含まれないこと
        assert "activity_id" not in result

    def test_get_material_has_hint(self, temp_db):
        """get_materialのレスポンスにhintフィールドが含まれる"""
        created = add_material(
            title="Hint Test",
            content="Content for hint test",
            tags=["domain:test"],
        )

        result = get_material(created["material_id"])

        assert "error" not in result
        assert "hint" in result
        assert "snippet" in result["hint"]

    def test_get_material_not_found(self, temp_db):
        """存在しないmaterial_idでNOT_FOUNDエラーになる"""
        result = get_material(9999)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"


class TestGetByIdMaterial:
    """get_by_id / get_by_ids でmaterialを取得するテスト"""

    def test_get_by_id_material(self, temp_db):
        """get_by_idでmaterialを取得できる"""
        created = add_material(
            title="ById Test",
            content="ById content",
            tags=["domain:test"],
        )
        material_id = created["material_id"]

        result = get_by_id("material", material_id)

        assert "error" not in result
        assert result["type"] == "material"
        assert result["data"]["material_id"] == material_id
        assert result["data"]["title"] == "ById Test"
        assert "content" not in result["data"]  # カタログ形式: 全文なし
        assert result["data"]["tags"] == ["domain:test"]  # material自身のタグ
        # activity_idが含まれないこと
        assert "activity_id" not in result["data"]

    def test_get_by_id_material_not_found(self, temp_db):
        """存在しないmaterial_idでNOT_FOUNDエラーになる"""
        result = get_by_id("material", 9999)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"

    def test_get_by_ids_material(self, temp_db):
        """get_by_idsでmaterialを取得できる"""
        created = add_material(
            title="Batch Test",
            content="Batch content",
            tags=["domain:test"],
        )
        material_id = created["material_id"]

        result = get_by_ids([{"type": "material", "id": material_id}])

        assert "error" not in result
        assert len(result["results"]) == 1
        assert result["results"][0]["type"] == "material"
        assert result["results"][0]["data"]["material_id"] == material_id

    def test_get_by_ids_mixed_types(self, activity_id):
        """get_by_idsでmaterialと他のtypeを混在して取得できる"""
        created = add_material(
            title="Mixed Test",
            content="Mixed content",
            tags=["domain:test"],
            related=[{"type": "activity", "ids": [activity_id]}],
        )
        material_id = created["material_id"]

        result = get_by_ids([
            {"type": "material", "id": material_id},
            {"type": "activity", "id": activity_id},
        ])

        assert "error" not in result
        assert len(result["results"]) == 2
        # material
        assert result["results"][0]["type"] == "material"
        assert result["results"][0]["data"]["material_id"] == material_id
        # activity
        assert result["results"][1]["type"] == "activity"
        assert result["results"][1]["data"]["id"] == activity_id


class TestUpdateMaterial:
    """update_material integration tests"""

    def _create_material(self):
        """Helper to create a material for update tests"""
        return add_material(
            title="Original Title",
            content="Original content",
            tags=["domain:test", "design"],
        )

    def test_update_content(self, temp_db):
        """Updating content only succeeds"""
        created = self._create_material()
        material_id = created["material_id"]

        result = update_material(material_id, content="Updated content")

        assert "error" not in result
        assert result["material_id"] == material_id
        assert result["content"] == "Updated content"
        assert result["title"] == "Original Title"  # unchanged
        assert "hint" in result
        assert sorted(result["tags"]) == sorted(["design", "domain:test"])

    def test_update_title(self, temp_db):
        """Updating title only succeeds"""
        created = self._create_material()
        material_id = created["material_id"]

        result = update_material(material_id, title="Updated Title")

        assert "error" not in result
        assert result["material_id"] == material_id
        assert result["title"] == "Updated Title"
        assert result["content"] == "Original content"  # unchanged
        assert "hint" in result
        assert sorted(result["tags"]) == sorted(["design", "domain:test"])

    def test_update_both(self, temp_db):
        """Updating both content and title succeeds"""
        created = self._create_material()
        material_id = created["material_id"]

        result = update_material(material_id, content="New content", title="New Title")

        assert "error" not in result
        assert result["material_id"] == material_id
        assert result["title"] == "New Title"
        assert result["content"] == "New content"
        assert "hint" in result

    def test_update_neither_returns_validation_error(self, temp_db):
        """Providing neither content nor title returns VALIDATION_ERROR"""
        created = self._create_material()
        material_id = created["material_id"]

        result = update_material(material_id)

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "At least one" in result["error"]["message"]

    def test_update_not_found(self, temp_db):
        """Non-existent material_id returns NOT_FOUND"""
        result = update_material(9999, content="New content")

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"

    def test_update_empty_title(self, temp_db):
        """Empty title returns VALIDATION_ERROR"""
        created = self._create_material()
        material_id = created["material_id"]

        result = update_material(material_id, title="")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "title" in result["error"]["message"]

    def test_update_persists_via_get_material(self, temp_db):
        """get_materialで更新が永続化されていることを確認"""
        created = self._create_material()
        material_id = created["material_id"]

        update_material(material_id, content="Persisted content", title="Persisted Title")

        fetched = get_material(material_id)
        assert fetched["title"] == "Persisted Title"
        assert fetched["content"] == "Persisted content"
        assert sorted(fetched["tags"]) == sorted(["design", "domain:test"])

    def test_update_empty_content(self, temp_db):
        """Empty content returns VALIDATION_ERROR"""
        created = self._create_material()
        material_id = created["material_id"]

        result = update_material(material_id, content="")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "content" in result["error"]["message"]
