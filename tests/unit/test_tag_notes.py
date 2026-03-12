"""タグ notes 機能のユニットテスト

- update_tag の正常系・エラー系
- list_tags の notes 返却
- 遭遇時注入の正常系・重複防止
- get_by_ids での遭遇時注入
"""
import os
import tempfile
import pytest
from src.db import init_database, get_connection
from src.services.tag_service import (
    update_tag,
    list_tags,
    ensure_tag_ids,
    collect_tag_notes_for_injection,
    _injected_tags,
)
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.search_service import get_by_ids
import src.services.embedding_service as emb


@pytest.fixture(autouse=True)
def disable_embedding(monkeypatch):
    """embeddingサービスを無効化"""
    monkeypatch.setattr(emb, '_server_initialized', False)
    monkeypatch.setattr(emb, '_backfill_done', True)
    monkeypatch.setattr(emb, '_ensure_server_running', lambda: False)


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


@pytest.fixture(autouse=True)
def reset_injected_tags():
    """各テスト前に注入済みタグをリセットする"""
    _injected_tags.clear()
    yield
    _injected_tags.clear()


# ========================================
# update_tag テスト
# ========================================


class TestUpdateTag:
    """update_tagのテスト"""

    def test_update_existing_tag(self, temp_db):
        """既存タグに notes を設定できる"""
        # タグを作成
        add_topic(title="Test", description="Desc", tags=["domain:test"])

        result = update_tag("domain:test", "このドメインでは注意が必要")
        assert "error" not in result
        assert result["tag"] == "domain:test"
        assert result["notes"] == "このドメインでは注意が必要"
        assert result["updated"] is True

    def test_update_bare_tag(self, temp_db):
        """素タグに notes を設定できる"""
        add_topic(title="Test", description="Desc", tags=["hooks"])

        result = update_tag("hooks", "hookの教訓")
        assert "error" not in result
        assert result["tag"] == "hooks"
        assert result["notes"] == "hookの教訓"
        assert result["updated"] is True

    def test_update_nonexistent_tag(self, temp_db):
        """存在しないタグでNOT_FOUNDエラー"""
        result = update_tag("domain:nonexistent", "notes text")
        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"

    def test_update_overwrite(self, temp_db):
        """notes を上書きできる（全文置換）"""
        add_topic(title="Test", description="Desc", tags=["domain:test"])

        update_tag("domain:test", "初回 notes")
        result = update_tag("domain:test", "更新後 notes")
        assert result["notes"] == "更新後 notes"


# ========================================
# list_tags + notes テスト
# ========================================


class TestListTagsWithNotes:
    """list_tags の notes 返却テスト"""

    def test_notes_included_in_response(self, temp_db):
        """list_tags の各タグに notes フィールドが含まれる"""
        add_topic(title="Test", description="Desc", tags=["domain:test"])
        update_tag("domain:test", "テスト用 notes")

        result = list_tags()
        assert "error" not in result
        test_tag = next(t for t in result["tags"] if t["tag"] == "domain:test")
        assert "notes" in test_tag
        assert test_tag["notes"] == "テスト用 notes"

    def test_notes_null_when_not_set(self, temp_db):
        """notes 未設定タグは None"""
        add_topic(title="Test", description="Desc", tags=["domain:test"])

        result = list_tags()
        assert "error" not in result
        test_tag = next(t for t in result["tags"] if t["tag"] == "domain:test")
        assert test_tag["notes"] is None


# ========================================
# 遭遇時注入テスト
# ========================================


class TestTagNotesInjection:
    """遭遇時注入ロジックのテスト"""

    def test_first_encounter_injects_notes(self, temp_db):
        """初回遭遇で notes が付加される"""
        add_topic(title="Test", description="Desc", tags=["domain:test"])
        update_tag("domain:test", "重要な教訓")

        conn = get_connection()
        try:
            result = collect_tag_notes_for_injection(conn, ["domain:test"])
            assert result is not None
            assert len(result) == 1
            assert result[0]["tag"] == "domain:test"
            assert result[0]["notes"] == "重要な教訓"
        finally:
            conn.close()

    def test_second_encounter_no_injection(self, temp_db):
        """2回目の遭遇では注入されない"""
        add_topic(title="Test", description="Desc", tags=["domain:test"])
        update_tag("domain:test", "重要な教訓")

        conn = get_connection()
        try:
            # 1回目
            result1 = collect_tag_notes_for_injection(conn, ["domain:test"])
            assert result1 is not None

            # 2回目
            result2 = collect_tag_notes_for_injection(conn, ["domain:test"])
            assert result2 is None
        finally:
            conn.close()

    def test_no_notes_returns_none(self, temp_db):
        """notes がないタグでは None が返る"""
        add_topic(title="Test", description="Desc", tags=["domain:test"])

        conn = get_connection()
        try:
            result = collect_tag_notes_for_injection(conn, ["domain:test"])
            assert result is None
        finally:
            conn.close()

    def test_mixed_tags_with_and_without_notes(self, temp_db):
        """notes があるタグとないタグの混在"""
        add_topic(title="Test", description="Desc", tags=["domain:test", "domain:empty"])
        update_tag("domain:test", "テスト教訓")
        # domain:empty には notes を設定しない

        conn = get_connection()
        try:
            result = collect_tag_notes_for_injection(conn, ["domain:test", "domain:empty"])
            assert result is not None
            assert len(result) == 1
            assert result[0]["tag"] == "domain:test"
        finally:
            conn.close()

    def test_multiple_tags_with_notes(self, temp_db):
        """複数タグに notes がある場合"""
        add_topic(title="Test", description="Desc", tags=["domain:test", "intent:design"])
        update_tag("domain:test", "テスト教訓")
        update_tag("intent:design", "設計の教訓")

        conn = get_connection()
        try:
            result = collect_tag_notes_for_injection(conn, ["domain:test", "intent:design"])
            assert result is not None
            assert len(result) == 2
            tag_strs = {r["tag"] for r in result}
            assert "domain:test" in tag_strs
            assert "intent:design" in tag_strs
        finally:
            conn.close()

    def test_partial_new_tags(self, temp_db):
        """一部が既に遭遇済み、一部が新規の場合"""
        add_topic(title="Test", description="Desc", tags=["domain:test", "intent:design"])
        update_tag("domain:test", "テスト教訓")
        update_tag("intent:design", "設計の教訓")

        conn = get_connection()
        try:
            # domain:test だけ先に遭遇
            collect_tag_notes_for_injection(conn, ["domain:test"])

            # 両方渡すが、domain:test は既に遭遇済み
            result = collect_tag_notes_for_injection(conn, ["domain:test", "intent:design"])
            assert result is not None
            assert len(result) == 1
            assert result[0]["tag"] == "intent:design"
        finally:
            conn.close()

    def test_nonexistent_tag_no_error(self, temp_db):
        """DBに存在しないタグを渡してもエラーにならない"""
        conn = get_connection()
        try:
            result = collect_tag_notes_for_injection(conn, ["domain:nonexistent"])
            assert result is None
        finally:
            conn.close()


# ========================================
# 常時注入（always_inject_namespaces）テスト
# ========================================


class TestAlwaysInjectNamespaces:
    """always_inject_namespaces パラメータのテスト"""

    def test_always_inject_returns_notes_every_time(self, temp_db):
        """always_inject_namespaces 対象のタグは毎回 notes を返す"""
        add_topic(title="Test", description="Desc", tags=["intent:design"])
        update_tag("intent:design", "設計の教訓")

        conn = get_connection()
        try:
            # 1回目
            result1 = collect_tag_notes_for_injection(
                conn, ["intent:design"], always_inject_namespaces=["intent"]
            )
            assert result1 is not None
            assert len(result1) == 1
            assert result1[0]["tag"] == "intent:design"

            # 2回目: 通常なら None だが、always_inject なので返る
            result2 = collect_tag_notes_for_injection(
                conn, ["intent:design"], always_inject_namespaces=["intent"]
            )
            assert result2 is not None
            assert len(result2) == 1
            assert result2[0]["tag"] == "intent:design"
        finally:
            conn.close()

    def test_always_inject_does_not_register_in_injected_tags(self, temp_db):
        """always_inject 対象のタグは _injected_tags に登録されない"""
        add_topic(title="Test", description="Desc", tags=["intent:design"])
        update_tag("intent:design", "設計の教訓")

        conn = get_connection()
        try:
            collect_tag_notes_for_injection(
                conn, ["intent:design"], always_inject_namespaces=["intent"]
            )
            assert "intent:design" not in _injected_tags
        finally:
            conn.close()

    def test_normal_tags_still_deduplicated(self, temp_db):
        """always_inject_namespaces を使っても通常タグは従来通り重複防止される"""
        add_topic(title="Test", description="Desc", tags=["domain:test", "intent:design"])
        update_tag("domain:test", "テスト教訓")
        update_tag("intent:design", "設計の教訓")

        conn = get_connection()
        try:
            # 1回目: 両方返る
            result1 = collect_tag_notes_for_injection(
                conn, ["domain:test", "intent:design"],
                always_inject_namespaces=["intent"],
            )
            assert result1 is not None
            assert len(result1) == 2

            # 2回目: domain:test は既に注入済みなので intent:design だけ
            result2 = collect_tag_notes_for_injection(
                conn, ["domain:test", "intent:design"],
                always_inject_namespaces=["intent"],
            )
            assert result2 is not None
            assert len(result2) == 1
            assert result2[0]["tag"] == "intent:design"
        finally:
            conn.close()

    def test_always_inject_no_notes_returns_none(self, temp_db):
        """always_inject 対象でも notes がなければ None が返る"""
        # domain:nonotes に notes を設定しない
        add_topic(title="Test", description="Desc", tags=["domain:nonotes"])

        conn = get_connection()
        try:
            result = collect_tag_notes_for_injection(
                conn, ["domain:nonotes"], always_inject_namespaces=["domain"]
            )
            assert result is None
        finally:
            conn.close()

    def test_always_inject_with_no_parameter(self, temp_db):
        """always_inject_namespaces 未指定の場合、従来通りの動作"""
        add_topic(title="Test", description="Desc", tags=["intent:design"])
        update_tag("intent:design", "設計の教訓")

        conn = get_connection()
        try:
            # 1回目
            result1 = collect_tag_notes_for_injection(conn, ["intent:design"])
            assert result1 is not None

            # 2回目: 従来通り None
            result2 = collect_tag_notes_for_injection(conn, ["intent:design"])
            assert result2 is None
        finally:
            conn.close()

    def test_multiple_always_inject_namespaces(self, temp_db):
        """複数の namespace を always_inject に指定できる"""
        add_topic(title="Test", description="Desc", tags=["intent:design", "domain:test"])
        update_tag("intent:design", "設計の教訓")
        update_tag("domain:test", "テスト教訓")

        conn = get_connection()
        try:
            # 1回目
            result1 = collect_tag_notes_for_injection(
                conn, ["intent:design", "domain:test"],
                always_inject_namespaces=["intent", "domain"],
            )
            assert result1 is not None
            assert len(result1) == 2

            # 2回目: 両方 always なので両方返る
            result2 = collect_tag_notes_for_injection(
                conn, ["intent:design", "domain:test"],
                always_inject_namespaces=["intent", "domain"],
            )
            assert result2 is not None
            assert len(result2) == 2
        finally:
            conn.close()


# ========================================
# get_by_ids 遭遇時注入テスト
# ========================================


class TestGetByIdsInjection:
    """get_by_ids での遭遇時注入テスト

    main.py の @mcp.tool() デコレータ付き関数は FunctionTool になるため直接呼べない。
    search_service.get_by_ids + _maybe_inject_tag_notes の組み合わせをテストする。
    """

    def test_get_by_ids_injects_tag_notes(self, temp_db):
        """get_by_ids の結果からタグ notes が注入される"""
        from src.main import _maybe_inject_tag_notes

        topic = add_topic(title="Test Topic", description="Desc", tags=["domain:test"])
        update_tag("domain:test", "テスト教訓")

        # search_service.get_by_ids で結果取得
        result = get_by_ids([{"type": "topic", "id": topic["topic_id"]}])
        assert "error" not in result

        # main.py と同じパターンで注入
        all_tags = []
        for item in result.get("results", []):
            if "data" in item:
                all_tags.extend(item["data"].get("tags", []))
        if all_tags:
            _maybe_inject_tag_notes(result, all_tags)

        assert "tag_notes" in result
        assert len(result["tag_notes"]) == 1
        assert result["tag_notes"][0]["tag"] == "domain:test"
        assert result["tag_notes"][0]["notes"] == "テスト教訓"

    def test_get_by_ids_no_notes_no_key(self, temp_db):
        """notes がない場合は tag_notes キーが含まれない"""
        from src.main import _maybe_inject_tag_notes

        topic = add_topic(title="Test Topic", description="Desc", tags=["domain:test"])

        result = get_by_ids([{"type": "topic", "id": topic["topic_id"]}])
        assert "error" not in result

        all_tags = []
        for item in result.get("results", []):
            if "data" in item:
                all_tags.extend(item["data"].get("tags", []))
        if all_tags:
            _maybe_inject_tag_notes(result, all_tags)

        assert "tag_notes" not in result
