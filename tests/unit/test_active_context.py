"""_build_activities_section および関連ヘルパー関数のユニットテスト

データ取得関数はsrc/services/activity_service.pyに、
表示整形関数はhooks/session_start_hook.pyに配置されている。
"""
import os
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from src.db import init_database, get_connection
from src.services.topic_service import add_topic
from src.services.activity_service import (
    add_activity,
    update_activity,
    get_active_domains,
    get_active_activities_by_tag,
)
import src.services.embedding_service as emb
from hooks.session_start_hook import (
    _build_activities_section,
    _calc_elapsed_days,
    IN_PROGRESS_LIMIT,
    PENDING_LIMIT,
)


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


def _get_tag_id(namespace: str, name: str) -> int:
    """テスト用: タグIDを取得する"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM tags WHERE namespace = ? AND name = ?",
            (namespace, name),
        ).fetchone()
        return row["id"] if row else -1
    finally:
        conn.close()


def _build_active_context_wrapper():
    """テスト用: connを自動管理してアクティビティセクションを組み立てる"""
    conn = get_connection()
    try:
        return _build_activities_section(conn)
    finally:
        conn.close()


# ========================================
# 定数の確認
# ========================================


def test_constants():
    """定数値が仕様通り"""
    assert IN_PROGRESS_LIMIT == 3
    assert PENDING_LIMIT == 2


# ========================================
# _calc_elapsed_days のテスト
# ========================================


def test_calc_elapsed_days_today():
    """本日更新なら0日"""
    now = datetime.now(timezone.utc).isoformat()
    assert _calc_elapsed_days(now) == 0


def test_calc_elapsed_days_3_days_ago():
    """3日前なら3"""
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert _calc_elapsed_days(three_days_ago) == 3


def test_calc_elapsed_days_sqlite_format():
    """SQLiteのCURRENT_TIMESTAMP形式（スペース区切り、TZ情報なし）でも正しく計算"""
    assert _calc_elapsed_days("2026-03-14 10:00:00") >= 0


def test_calc_elapsed_days_invalid_string():
    """不正な文字列なら0"""
    assert _calc_elapsed_days("not-a-date") == 0


def test_calc_elapsed_days_none():
    """Noneなら0"""
    assert _calc_elapsed_days(None) == 0


def test_calc_elapsed_days_empty():
    """空文字列なら0"""
    assert _calc_elapsed_days("") == 0


# ========================================
# get_active_domains のテスト
# ========================================


def test_get_active_domains_with_active_activity(temp_db):
    """アクティブなアクティビティがあるdomainが返る"""
    add_activity(
        title="Activity 1", description="Desc",
        tags=["domain:myproject"], check_in=False,
    )

    domains = get_active_domains()
    names = [d["name"] for d in domains]
    assert "myproject" in names


def test_get_active_domains_excludes_completed(temp_db):
    """completedアクティビティのみのdomainは返らない"""
    result = add_activity(
        title="Done", description="Desc",
        tags=["domain:completed-proj"], check_in=False,
    )
    update_activity(result["activity_id"], status="completed")

    domains = get_active_domains()
    names = [d["name"] for d in domains]
    assert "completed-proj" not in names


def test_get_active_domains_excludes_non_domain(temp_db):
    """domain以外のnamespaceは返らない"""
    add_activity(
        title="Activity 1", description="Desc",
        tags=["intent:design"], check_in=False,
    )

    domains = get_active_domains()
    names = [d["name"] for d in domains]
    assert "design" not in names


def test_get_active_domains_sorted_by_name(temp_db):
    """name順ソート"""
    add_activity(title="Z", description="Desc", tags=["domain:zzz"], check_in=False)
    add_activity(title="A", description="Desc", tags=["domain:aaa"], check_in=False)

    domains = get_active_domains()
    names = [d["name"] for d in domains]
    aaa_idx = names.index("aaa")
    zzz_idx = names.index("zzz")
    assert aaa_idx < zzz_idx


def test_get_active_domains_deduplicates(temp_db):
    """同じdomain:タグを持つ複数アクティビティで重複しない"""
    add_activity(title="A1", description="Desc", tags=["domain:myproject"], check_in=False)
    add_activity(title="A2", description="Desc", tags=["domain:myproject"], check_in=False)

    domains = get_active_domains()
    myproject_domains = [d for d in domains if d["name"] == "myproject"]
    assert len(myproject_domains) == 1


def test_get_active_domains_no_activities(temp_db):
    """アクティビティがないdomainは返らない（トピックだけでは返らない）"""
    add_topic(title="Topic Only", description="Desc", tags=["domain:topic-only-proj"])

    domains = get_active_domains()
    names = [d["name"] for d in domains]
    assert "topic-only-proj" not in names


# ========================================
# get_active_activities_by_tag のテスト
# ========================================


def test_get_active_activities_by_tag_basic(temp_db):
    """domain:タグに紐づくホットアクティビティが返る"""
    add_activity(title="Activity 1", description="Desc", tags=["domain:test-proj"], check_in=False)

    tag_id = _get_tag_id("domain", "test-proj")
    activities = get_active_activities_by_tag(tag_id)

    assert len(activities) == 1
    assert activities[0]["title"] == "Activity 1"
    assert activities[0]["status"] == "pending"


def test_get_active_activities_by_tag_has_updated_at(temp_db):
    """updated_atフィールドが含まれる"""
    add_activity(title="Activity 1", description="Desc", tags=["domain:test-proj"], check_in=False)

    tag_id = _get_tag_id("domain", "test-proj")
    activities = get_active_activities_by_tag(tag_id)

    assert "updated_at" in activities[0]
    assert activities[0]["updated_at"] is not None


def test_get_active_activities_by_tag_excludes_completed(temp_db):
    """completedアクティビティは含まれない"""
    result = add_activity(title="Done Activity", description="Desc", tags=["domain:test-proj"], check_in=False)
    update_activity(result["activity_id"], status="completed")

    tag_id = _get_tag_id("domain", "test-proj")
    activities = get_active_activities_by_tag(tag_id)

    assert len(activities) == 0


def test_get_active_activities_by_tag_sort_order(temp_db):
    """in_progressが先、その後pending"""
    r1 = add_activity(title="Pending Activity", description="Desc", tags=["domain:test-proj"], check_in=False)
    r2 = add_activity(title="In Progress Activity", description="Desc", tags=["domain:test-proj"], check_in=False)
    update_activity(r2["activity_id"], status="in_progress")

    tag_id = _get_tag_id("domain", "test-proj")
    activities = get_active_activities_by_tag(tag_id)

    assert len(activities) == 2
    assert activities[0]["status"] == "in_progress"
    assert activities[1]["status"] == "pending"


def test_get_active_activities_by_tag_empty(temp_db):
    """紐づくアクティビティがなければ空リスト"""
    add_topic(title="Topic Only", description="Desc", tags=["domain:no-activities"])

    tag_id = _get_tag_id("domain", "no-activities")
    activities = get_active_activities_by_tag(tag_id)

    assert activities == []


# ========================================
# _build_activities_section のテスト
# ========================================


def test_build_activities_section_empty(temp_db):
    """アクティブなアクティビティがない場合は空文字列"""
    result = _build_active_context_wrapper()
    assert result == ""


def test_build_activities_section_empty_with_only_topics(temp_db):
    """トピックだけでアクティビティがない場合も空文字列"""
    add_topic(title="Topic Only", description="Desc", tags=["domain:myapp"])

    result = _build_active_context_wrapper()
    assert result == ""


def test_build_activities_section_with_activities(temp_db):
    """アクティビティがある場合、domainセクションが生成される"""
    add_activity(title="[作業] 実装する", description="Desc", tags=["domain:myapp"], check_in=False)

    result = _build_active_context_wrapper()

    assert "# アクティビティ一覧" in result
    assert "## myapp (domain)" in result
    assert "[作業] 実装する" in result


def test_build_activities_section_status_marker_pending(temp_db):
    """pendingアクティビティに○マーカーが付く"""
    add_activity(title="[作業] 実装する", description="Desc", tags=["domain:myapp"], check_in=False)

    result = _build_active_context_wrapper()

    assert "\u25cb" in result


def test_build_activities_section_status_marker_in_progress(temp_db):
    """in_progressアクティビティに●マーカーが付く"""
    r = add_activity(title="[作業] 実装する", description="Desc", tags=["domain:myapp"], check_in=False)
    update_activity(r["activity_id"], status="in_progress")

    result = _build_active_context_wrapper()

    assert "\u25cf" in result


def test_build_activities_section_elapsed_days(temp_db):
    """経過日数が(Nd)形式で表示される"""
    add_activity(title="[作業] 実装する", description="Desc", tags=["domain:myapp"], check_in=False)

    result = _build_active_context_wrapper()

    # 作成直後なので(0d)が表示される
    assert "(0d)" in result


def test_build_activities_section_no_topic_section(temp_db):
    """トピックセクション（最新トピック:）が出力されない"""
    add_topic(title="My Topic", description="Desc", tags=["domain:myapp"])
    add_activity(title="[作業] 実装する", description="Desc", tags=["domain:myapp"], check_in=False)

    result = _build_active_context_wrapper()

    assert "最新トピック:" not in result
    assert "My Topic" not in result


def test_build_activities_section_no_recent_tags_section(temp_db):
    """最近使われたタグセクションが出力されない"""
    add_topic(title="Topic", description="Desc", tags=["domain:myapp", "intent:design", "hooks"])
    add_activity(title="[作業] 実装する", description="Desc", tags=["domain:myapp"], check_in=False)

    result = _build_active_context_wrapper()

    assert "## 最近使われたタグ" not in result


def test_build_activities_section_in_progress_limit(temp_db):
    """in_progress枠は上位IN_PROGRESS_LIMIT件に制限される"""
    for i in range(5):
        r = add_activity(
            title=f"[作業] IP Activity {i}", description="Desc",
            tags=["domain:myapp"], check_in=False,
        )
        update_activity(r["activity_id"], status="in_progress")

    result = _build_active_context_wrapper()

    # IN_PROGRESS_LIMIT=3件分の●が表示される
    assert result.count("\u25cf") == IN_PROGRESS_LIMIT


def test_build_activities_section_pending_limit(temp_db):
    """pending枠は上位PENDING_LIMIT件に制限される"""
    for i in range(5):
        add_activity(
            title=f"[作業] Pending Activity {i}", description="Desc",
            tags=["domain:myapp"], check_in=False,
        )

    result = _build_active_context_wrapper()

    # PENDING_LIMIT=2件分の○が表示される
    assert result.count("\u25cb") == PENDING_LIMIT


def test_build_activities_section_overflow_count(temp_db):
    """制限を超えた分が(+N件)で表示される"""
    # in_progress 4件 + pending 3件 = 合計7件
    for i in range(4):
        r = add_activity(
            title=f"[作業] IP {i}", description="Desc",
            tags=["domain:myapp"], check_in=False,
        )
        update_activity(r["activity_id"], status="in_progress")
    for i in range(3):
        add_activity(
            title=f"[作業] Pending {i}", description="Desc",
            tags=["domain:myapp"], check_in=False,
        )

    result = _build_active_context_wrapper()

    # 表示: IP 3件 + Pending 2件 = 5件、overflow = 7 - 5 = 2件
    assert "(+2件)" in result


def test_build_activities_section_no_overflow_when_within_limits(temp_db):
    """件数が制限内の場合は(+N件)が出ない"""
    r = add_activity(
        title="[作業] IP 1", description="Desc",
        tags=["domain:myapp"], check_in=False,
    )
    update_activity(r["activity_id"], status="in_progress")
    add_activity(
        title="[作業] Pending 1", description="Desc",
        tags=["domain:myapp"], check_in=False,
    )

    result = _build_active_context_wrapper()

    assert "(+" not in result


def test_build_activities_section_domain_with_zero_activities_skipped(temp_db):
    """アクティビティ0件のdomainセクションはスキップ"""
    # domain:myappにはアクティビティあり、domain:emptyにはなし
    add_activity(title="Activity", description="Desc", tags=["domain:myapp"], check_in=False)

    # domain:emptyのタグだけ作成（アクティビティなし）
    conn = get_connection()
    try:
        from src.services.tag_service import ensure_tag_ids
        ensure_tag_ids(conn, [("domain", "empty-domain")])
        conn.commit()
    finally:
        conn.close()

    result = _build_active_context_wrapper()

    assert "## myapp (domain)" in result
    assert "empty-domain" not in result


def test_build_activities_section_multiple_domains(temp_db):
    """複数domainが個別セクションとして表示される"""
    add_activity(title="App Activity", description="Desc", tags=["domain:app"], check_in=False)
    add_activity(title="Lib Activity", description="Desc", tags=["domain:lib"], check_in=False)

    result = _build_active_context_wrapper()

    assert "## app (domain)" in result
    assert "## lib (domain)" in result


def test_build_activities_section_activity_id_in_bracket(temp_db):
    """アクティビティIDが[id]形式で表示される"""
    activity = add_activity(title="Activity 1", description="Desc", tags=["domain:myapp"], check_in=False)
    activity_id = activity["activity_id"]

    result = _build_active_context_wrapper()

    assert f"[{activity_id}]" in result


def test_build_activities_section_raises_on_invalid_db(temp_db):
    """DB接続失敗時は例外が発生する（hookのmain()がcatchする前提）"""
    os.environ["DISCUSSION_DB_PATH"] = "/nonexistent/path/test.db"

    with pytest.raises(Exception):
        _build_active_context_wrapper()

    # 元に戻す
    os.environ["DISCUSSION_DB_PATH"] = temp_db


def test_build_activities_section_completed_activities_excluded(temp_db):
    """completedアクティビティは表示されない"""
    result = add_activity(title="Done Activity", description="Desc", tags=["domain:myapp"], check_in=False)
    update_activity(result["activity_id"], status="completed")

    ctx = _build_active_context_wrapper()

    assert "Done Activity" not in ctx


def test_build_activities_section_format(temp_db):
    """出力フォーマットが仕様通り"""
    r = add_activity(
        title="[議論] stop_hookのスキップ機能", description="Desc",
        tags=["domain:cc-memory"], check_in=False,
    )
    update_activity(r["activity_id"], status="in_progress")
    add_activity(
        title="[作業] アクティブコンテキスト改善", description="Desc",
        tags=["domain:cc-memory"], check_in=False,
    )

    result = _build_active_context_wrapper()

    lines = result.strip().split("\n")
    assert lines[0] == "# アクティビティ一覧"
    assert lines[1] == ""
    assert lines[2] == "## cc-memory (domain)"
    # in_progressが先に来る
    assert lines[3].startswith("\u25cf")
    assert "[議論] stop_hookのスキップ機能" in lines[3]
    assert lines[3].endswith("(0d)")
    # pendingが後に来る
    assert lines[4].startswith("\u25cb")
    assert "[作業] アクティブコンテキスト改善" in lines[4]
    assert lines[4].endswith("(0d)")
