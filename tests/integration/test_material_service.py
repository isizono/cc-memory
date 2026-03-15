"""資材サービスの統合テスト"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.activity_service import add_activity
from src.services.material_service import add_material, get_material, list_materials
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

    def test_add_material_success(self, activity_id):
        """資材の追加が成功する"""
        result = add_material(
            activity_id=activity_id,
            title="Test Material",
            content="# Test Content\n\nThis is a test material.",
        )

        assert "error" not in result
        assert result["material_id"] > 0
        assert result["activity_id"] == activity_id
        assert result["title"] == "Test Material"
        assert result["content"] == "# Test Content\n\nThis is a test material."
        assert "created_at" in result

    def test_add_material_invalid_activity_id(self, temp_db):
        """存在しないactivity_idでNOT_FOUNDエラーになる"""
        result = add_material(
            activity_id=9999,
            title="Test Material",
            content="Content",
        )

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
        assert "Activity" in result["error"]["message"]

    def test_add_material_empty_title(self, activity_id):
        """空のtitleでVALIDATION_ERRORになる"""
        result = add_material(
            activity_id=activity_id,
            title="",
            content="Content",
        )

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "title" in result["error"]["message"]

    def test_add_material_whitespace_title(self, activity_id):
        """空白のみのtitleでVALIDATION_ERRORになる"""
        result = add_material(
            activity_id=activity_id,
            title="   ",
            content="Content",
        )

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_add_material_empty_content(self, activity_id):
        """空のcontentでVALIDATION_ERRORになる"""
        result = add_material(
            activity_id=activity_id,
            title="Title",
            content="",
        )

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "content" in result["error"]["message"]

    def test_add_material_whitespace_content(self, activity_id):
        """空白のみのcontentでVALIDATION_ERRORになる"""
        result = add_material(
            activity_id=activity_id,
            title="Title",
            content="   ",
        )

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_add_multiple_materials_to_same_activity(self, activity_id):
        """同一アクティビティに複数の資材を追加できる"""
        result1 = add_material(
            activity_id=activity_id,
            title="Material 1",
            content="Content 1",
        )
        result2 = add_material(
            activity_id=activity_id,
            title="Material 2",
            content="Content 2",
        )

        assert "error" not in result1
        assert "error" not in result2
        assert result1["material_id"] != result2["material_id"]
        assert result1["activity_id"] == result2["activity_id"] == activity_id


class TestGetMaterial:
    """get_materialの統合テスト"""

    def test_get_material_success(self, activity_id):
        """資材の全文取得が成功する"""
        created = add_material(
            activity_id=activity_id,
            title="Get Test",
            content="Full content here",
        )
        material_id = created["material_id"]

        result = get_material(material_id)

        assert "error" not in result
        assert result["material_id"] == material_id
        assert result["activity_id"] == activity_id
        assert result["title"] == "Get Test"
        assert result["content"] == "Full content here"
        assert "created_at" in result

    def test_get_material_not_found(self, temp_db):
        """存在しないmaterial_idでNOT_FOUNDエラーになる"""
        result = get_material(9999)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"


class TestGetByIdMaterial:
    """get_by_id / get_by_ids でmaterialを取得するテスト"""

    def test_get_by_id_material(self, activity_id):
        """get_by_idでmaterialを取得できる"""
        created = add_material(
            activity_id=activity_id,
            title="ById Test",
            content="ById content",
        )
        material_id = created["material_id"]

        result = get_by_id("material", material_id)

        assert "error" not in result
        assert result["type"] == "material"
        assert result["data"]["material_id"] == material_id
        assert result["data"]["activity_id"] == activity_id
        assert result["data"]["title"] == "ById Test"
        assert "content" not in result["data"]  # カタログ形式: 全文なし
        assert result["data"]["tags"] == ["domain:test"]  # activityのタグを継承

    def test_get_by_id_material_not_found(self, temp_db):
        """存在しないmaterial_idでNOT_FOUNDエラーになる"""
        result = get_by_id("material", 9999)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"

    def test_get_by_ids_material(self, activity_id):
        """get_by_idsでmaterialを取得できる"""
        created = add_material(
            activity_id=activity_id,
            title="Batch Test",
            content="Batch content",
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
            activity_id=activity_id,
            title="Mixed Test",
            content="Mixed content",
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


class TestListMaterials:
    """list_materialsの統合テスト"""

    def test_list_materials_success(self, activity_id):
        """アクティビティに紐づく資材一覧を取得できる"""
        add_material(activity_id=activity_id, title="Mat 1", content="Content 1")
        add_material(activity_id=activity_id, title="Mat 2", content="Content 2")

        result = list_materials(activity_id)

        assert "error" not in result
        assert result["activity_id"] == activity_id
        assert result["total_count"] == 2
        assert len(result["materials"]) == 2
        # カタログ形式: contentなし
        for m in result["materials"]:
            assert "material_id" in m
            assert "title" in m
            assert "created_at" in m
            assert "content" not in m

    def test_list_materials_empty(self, activity_id):
        """資材がないアクティビティでは空リストが返る"""
        result = list_materials(activity_id)

        assert "error" not in result
        assert result["total_count"] == 0
        assert result["materials"] == []

    def test_list_materials_invalid_activity_id(self, temp_db):
        """存在しないactivity_idでNOT_FOUNDエラーになる"""
        result = list_materials(9999)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
