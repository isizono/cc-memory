"""_build_active_context および関連ヘルパー関数のユニットテスト"""
import os
import tempfile
import pytest
from src.db import init_database, get_connection
from src.services.topic_service import add_topic
from src.services.task_service import add_task, update_task
import src.services.embedding_service as emb
from src.main import (
    _build_active_context,
    _get_active_domains,
    _get_recent_topics_by_tag,
    _get_active_tasks_by_tag,
    _get_recent_non_domain_tags,
    _truncate_desc,
    ACTIVE_DAYS,
    RECENT_TOPICS_LIMIT,
    DESC_MAX_LEN,
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


# ========================================
# 定数の確認
# ========================================


def test_constants():
    """定数値が旧実装と同じ"""
    assert ACTIVE_DAYS == 7
    assert RECENT_TOPICS_LIMIT == 3
    assert DESC_MAX_LEN == 30


# ========================================
# _truncate_desc のテスト
# ========================================


def test_truncate_desc_short():
    """30文字以下はそのまま"""
    assert _truncate_desc("短い説明") == "短い説明"


def test_truncate_desc_exact():
    """ちょうど30文字はそのまま"""
    text = "a" * 30
    assert _truncate_desc(text) == text


def test_truncate_desc_long():
    """31文字以上は切り詰め+..."""
    text = "a" * 31
    assert _truncate_desc(text) == "a" * 30 + "..."


def test_truncate_desc_empty():
    """空文字列"""
    assert _truncate_desc("") == ""


def test_truncate_desc_none():
    """None"""
    assert _truncate_desc(None) == ""


# ========================================
# _get_active_domains のテスト
# ========================================


def test_get_active_domains_basic(temp_db):
    """domain:タグのあるトピックがあれば返る"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:myproject"])

    domains = _get_active_domains()
    names = [d["name"] for d in domains]
    assert "myproject" in names


def test_get_active_domains_excludes_non_domain(temp_db):
    """domain以外のnamespaceは返らない"""
    add_topic(title="Topic 1", description="Desc", tags=["scope:search"])

    domains = _get_active_domains()
    names = [d["name"] for d in domains]
    assert "search" not in names


def test_get_active_domains_sorted_by_name(temp_db):
    """name順ソート"""
    add_topic(title="Topic Z", description="Desc", tags=["domain:zzz"])
    add_topic(title="Topic A", description="Desc", tags=["domain:aaa"])

    domains = _get_active_domains()
    # defaultも含む可能性があるのでフィルタ
    names = [d["name"] for d in domains]
    aaa_idx = names.index("aaa")
    zzz_idx = names.index("zzz")
    assert aaa_idx < zzz_idx


def test_get_active_domains_excludes_old_topics(temp_db):
    """7日以上前のトピックのdomain:タグは返らない"""
    # 古いトピックを直接INSERTする
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO discussion_topics (title, description, created_at) "
            "VALUES (?, ?, datetime('now', '-8 days'))",
            ("Old Topic", "Desc"),
        )
        topic_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        from src.services.tag_service import ensure_tag_ids, link_tags
        tag_ids = ensure_tag_ids(conn, [("domain", "old-project")])
        link_tags(conn, "topic_tags", "topic_id", topic_id, tag_ids)

        conn.commit()
    finally:
        conn.close()

    domains = _get_active_domains()
    names = [d["name"] for d in domains]
    assert "old-project" not in names


def test_get_active_domains_deduplicates(temp_db):
    """同じdomain:タグを持つ複数トピックで重複しない"""
    add_topic(title="Topic 1", description="Desc 1", tags=["domain:myproject"])
    add_topic(title="Topic 2", description="Desc 2", tags=["domain:myproject"])

    domains = _get_active_domains()
    myproject_domains = [d for d in domains if d["name"] == "myproject"]
    assert len(myproject_domains) == 1


# ========================================
# _get_recent_topics_by_tag のテスト
# ========================================


def test_get_recent_topics_by_tag_basic(temp_db):
    """domain:タグに紐づくトピックが返る"""
    add_topic(title="Topic A", description="Desc A", tags=["domain:test-proj"])

    tag_id = _get_tag_id("domain", "test-proj")
    topics = _get_recent_topics_by_tag(tag_id)

    assert len(topics) >= 1
    titles = [t["title"] for t in topics]
    assert "Topic A" in titles


def test_get_recent_topics_by_tag_limit(temp_db):
    """最大RECENT_TOPICS_LIMIT件"""
    for i in range(5):
        add_topic(title=f"Topic {i}", description=f"Desc {i}", tags=["domain:test-proj"])

    tag_id = _get_tag_id("domain", "test-proj")
    topics = _get_recent_topics_by_tag(tag_id)

    assert len(topics) == RECENT_TOPICS_LIMIT


def test_get_recent_topics_by_tag_order(temp_db):
    """新しい順"""
    add_topic(title="First", description="Desc 1", tags=["domain:test-proj"])
    add_topic(title="Second", description="Desc 2", tags=["domain:test-proj"])
    add_topic(title="Third", description="Desc 3", tags=["domain:test-proj"])

    tag_id = _get_tag_id("domain", "test-proj")
    topics = _get_recent_topics_by_tag(tag_id)

    assert topics[0]["title"] == "Third"
    assert topics[1]["title"] == "Second"
    assert topics[2]["title"] == "First"


def test_get_recent_topics_by_tag_empty(temp_db):
    """紐づくトピックがなければ空リスト"""
    # タグだけ作成
    conn = get_connection()
    try:
        from src.services.tag_service import ensure_tag_ids
        tag_ids = ensure_tag_ids(conn, [("domain", "empty-proj")])
        conn.commit()
    finally:
        conn.close()

    tag_id = _get_tag_id("domain", "empty-proj")
    topics = _get_recent_topics_by_tag(tag_id)
    assert topics == []


# ========================================
# _get_active_tasks_by_tag のテスト
# ========================================


def test_get_active_tasks_by_tag_basic(temp_db):
    """domain:タグに紐づくアクティブタスクが返る"""
    add_task(title="Task 1", description="Desc", tags=["domain:test-proj"])

    tag_id = _get_tag_id("domain", "test-proj")
    tasks = _get_active_tasks_by_tag(tag_id)

    assert len(tasks) == 1
    assert tasks[0]["title"] == "Task 1"
    assert tasks[0]["status"] == "pending"


def test_get_active_tasks_by_tag_excludes_completed(temp_db):
    """completedタスクは含まれない"""
    result = add_task(title="Done Task", description="Desc", tags=["domain:test-proj"])
    update_task(result["task_id"], new_status="completed")

    tag_id = _get_tag_id("domain", "test-proj")
    tasks = _get_active_tasks_by_tag(tag_id)

    assert len(tasks) == 0


def test_get_active_tasks_by_tag_sort_order(temp_db):
    """in_progressが先、その後pending"""
    r1 = add_task(title="Pending Task", description="Desc", tags=["domain:test-proj"])
    r2 = add_task(title="In Progress Task", description="Desc", tags=["domain:test-proj"])
    update_task(r2["task_id"], new_status="in_progress")

    tag_id = _get_tag_id("domain", "test-proj")
    tasks = _get_active_tasks_by_tag(tag_id)

    assert len(tasks) == 2
    assert tasks[0]["status"] == "in_progress"
    assert tasks[1]["status"] == "pending"


def test_get_active_tasks_by_tag_empty(temp_db):
    """紐づくタスクがなければ空リスト"""
    add_topic(title="Topic Only", description="Desc", tags=["domain:no-tasks"])

    tag_id = _get_tag_id("domain", "no-tasks")
    tasks = _get_active_tasks_by_tag(tag_id)

    assert tasks == []


# ========================================
# _get_recent_non_domain_tags のテスト
# ========================================


def test_get_recent_non_domain_tags_basic(temp_db):
    """domain:以外のタグが返る"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test", "scope:search", "hooks"])

    tags = _get_recent_non_domain_tags()
    assert "scope:search" in tags
    assert "hooks" in tags
    # domain:は含まれない
    assert "domain:test" not in tags


def test_get_recent_non_domain_tags_frequency_order(temp_db):
    """使用頻度降順"""
    # scope:searchを2回使用、modeを1回使用
    add_topic(title="Topic 1", description="Desc", tags=["domain:test", "scope:search"])
    add_topic(title="Topic 2", description="Desc", tags=["domain:test", "scope:search"])
    add_topic(title="Topic 3", description="Desc", tags=["domain:test", "mode:discuss"])

    tags = _get_recent_non_domain_tags()
    search_idx = tags.index("scope:search")
    mode_idx = tags.index("mode:discuss")
    assert search_idx < mode_idx


def test_get_recent_non_domain_tags_empty(temp_db):
    """domain:タグのみの場合は空"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test"])

    tags = _get_recent_non_domain_tags()
    assert tags == []


def test_get_recent_non_domain_tags_excludes_old(temp_db):
    """7日以上前のトピックのタグは返らない"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO discussion_topics (title, description, created_at) "
            "VALUES (?, ?, datetime('now', '-8 days'))",
            ("Old Topic", "Desc"),
        )
        topic_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        from src.services.tag_service import ensure_tag_ids, link_tags
        tag_ids = ensure_tag_ids(conn, [("scope", "old-scope")])
        link_tags(conn, "topic_tags", "topic_id", topic_id, tag_ids)

        conn.commit()
    finally:
        conn.close()

    tags = _get_recent_non_domain_tags()
    assert "scope:old-scope" not in tags


# ========================================
# _build_active_context のテスト
# ========================================


def test_build_active_context_empty(temp_db):
    """全トピックが期限切れで非domainタグもない場合は空文字列"""
    conn = get_connection()
    try:
        conn.execute("UPDATE discussion_topics SET created_at = datetime('now', '-8 days')")
        conn.commit()
    finally:
        conn.close()
    result = _build_active_context()
    assert result == ""


def test_build_active_context_with_domain_topics(temp_db):
    """domainトピックがある場合、セクションが生成される"""
    add_topic(title="My Topic", description="Topic description here", tags=["domain:myapp"])

    result = _build_active_context()

    assert "# アクティブコンテキスト" in result
    assert "## myapp (domain)" in result
    assert "最新トピック:" in result
    assert "My Topic" in result


def test_build_active_context_with_tasks(temp_db):
    """タスクがある場合、アクティブタスクセクションが生成される"""
    add_topic(title="Topic", description="Desc", tags=["domain:myapp"])
    add_task(title="[作業] 実装する", description="Desc", tags=["domain:myapp"])

    result = _build_active_context()

    assert "アクティブタスク:" in result
    assert "[作業] 実装する" in result
    assert "(pending)" in result


def test_build_active_context_description_truncated(temp_db):
    """descriptionが30文字を超えたら切り詰め"""
    long_desc = "a" * 50
    add_topic(title="Topic", description=long_desc, tags=["domain:myapp"])

    result = _build_active_context()

    # 30文字 + "..." が含まれる
    assert "a" * 30 + "..." in result
    # 50文字のフルテキストは含まれない
    assert "a" * 50 not in result


def test_build_active_context_non_domain_tags(temp_db):
    """domain:以外のタグが「最近使われたタグ」セクションに列挙される"""
    add_topic(title="Topic", description="Desc", tags=["domain:myapp", "scope:search", "hooks"])

    result = _build_active_context()

    assert "## 最近使われたタグ" in result
    assert "scope:search" in result
    assert "hooks" in result


def test_build_active_context_multiple_domains(temp_db):
    """複数domainが個別セクションとして表示される"""
    add_topic(title="App Topic", description="Desc", tags=["domain:app"])
    add_topic(title="Lib Topic", description="Desc", tags=["domain:lib"])

    result = _build_active_context()

    assert "## app (domain)" in result
    assert "## lib (domain)" in result


def test_build_active_context_topic_id_in_bracket(temp_db):
    """トピックIDが[id]形式で表示される"""
    topic = add_topic(title="My Topic", description="Desc", tags=["domain:myapp"])
    topic_id = topic["topic_id"]

    result = _build_active_context()

    assert f"[{topic_id}]" in result


def test_build_active_context_task_id_in_bracket(temp_db):
    """タスクIDが[id]形式で表示される"""
    add_topic(title="Topic", description="Desc", tags=["domain:myapp"])
    task = add_task(title="Task 1", description="Desc", tags=["domain:myapp"])
    task_id = task["task_id"]

    result = _build_active_context()

    assert f"[{task_id}]" in result


def test_build_active_context_no_crash_on_error(temp_db):
    """DB接続失敗などでもクラッシュしない"""
    # 無効なDB pathを設定
    os.environ["DISCUSSION_DB_PATH"] = "/nonexistent/path/test.db"

    result = _build_active_context()

    assert result == ""

    # 元に戻す
    os.environ["DISCUSSION_DB_PATH"] = temp_db


def test_build_active_context_completed_tasks_excluded(temp_db):
    """completedタスクはアクティブタスクに含まれない"""
    add_topic(title="Topic", description="Desc", tags=["domain:myapp"])
    result = add_task(title="Done Task", description="Desc", tags=["domain:myapp"])
    update_task(result["task_id"], new_status="completed")

    ctx = _build_active_context()

    assert "Done Task" not in ctx


def test_build_active_context_domain_no_topics_no_tasks_skipped(temp_db):
    """domain:タグはあるがtopic_tagsに紐付けないとdomainsに出てこない"""
    # domain:emptyタグだけ作成してトピックに紐付けない
    conn = get_connection()
    try:
        from src.services.tag_service import ensure_tag_ids
        ensure_tag_ids(conn, [("domain", "empty-domain")])
        conn.commit()
    finally:
        conn.close()

    result = _build_active_context()
    # topic_tagsにJOINしないdomainは表示されない
    assert "empty-domain" not in result


def test_build_active_context_only_non_domain_tags(temp_db):
    """domain:タグがなくnon-domainタグのみの場合"""
    # まずinit_databaseのfirst_topicを古くする
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE discussion_topics SET created_at = datetime('now', '-8 days')"
        )
        conn.commit()
    finally:
        conn.close()

    # non-domainタグのみのトピックを追加
    add_topic(title="Topic", description="Desc", tags=["scope:search"])

    result = _build_active_context()

    # domain:セクションはないがnon-domainタグセクションはある
    # ただしscope:searchのトピックにはdomain:タグがないので
    # domainセクションは生成されない
    assert "## 最近使われたタグ" in result
    assert "scope:search" in result
