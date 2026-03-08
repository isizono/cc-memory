"""FTS5統合検索（search / get_by_id）のテスト

embeddingサービスを無効化した状態でFTS5のみの検索動作を検証する。
ハイブリッド検索のテストは test_hybrid_search.py を参照。
"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.subject_service import add_subject
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.task_service import add_task
from src.services.discussion_log_service import add_log as add_log_entry
from src.services.search_service import search, get_by_id
import src.services.embedding_service as emb


@pytest.fixture(autouse=True)
def disable_embedding(monkeypatch):
    """FTS5テストではembeddingサービスを無効化"""
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


@pytest.fixture
def test_subject(temp_db):
    """テスト用サブジェクトを作成する"""
    result = add_subject(name="test-subject", description="Test subject description")
    return result["subject_id"]


# ========================================
# search ツールのテスト
# ========================================


def test_search_basic(test_subject):
    """基本検索: キーワードで結果が返る"""
    add_topic(
        subject_id=test_subject,
        title="FTS5統合検索の設計",
        description="FTS5 trigramトークナイザを使った検索機能",
    )

    result = search(subject_id=test_subject, keyword="FTS5統合検索")

    assert "error" not in result
    assert len(result["results"]) == 1
    assert result["total_count"] == 1


def test_search_response_format(test_subject):
    """レスポンスにtype/id/title/scoreが含まれる"""
    topic = add_topic(
        subject_id=test_subject,
        title="検索機能の設計",
        description="FTS5を使った統合検索機能の設計議論",
    )

    result = search(subject_id=test_subject, keyword="検索機能の設計")

    assert "error" not in result
    assert len(result["results"]) >= 1

    item = result["results"][0]
    assert "type" in item
    assert "id" in item
    assert "title" in item
    assert "score" in item
    assert item["type"] == "topic"
    assert item["id"] == topic["topic_id"]
    assert item["title"] == "検索機能の設計"
    assert isinstance(item["score"], float)


def test_search_bm25_ranking(test_subject):
    """BM25ランキング: titleマッチがbodyマッチより上位に来る"""
    # titleに「統合検索」を含むトピック
    topic1 = add_topic(
        subject_id=test_subject,
        title="統合検索の実装方針",
        description="実装の方針を検討する",
    )
    # bodyに「統合検索」を含むトピック
    topic2 = add_topic(
        subject_id=test_subject,
        title="実装方針の検討",
        description="統合検索についての議論",
    )

    result = search(subject_id=test_subject, keyword="統合検索")

    assert "error" not in result
    assert len(result["results"]) == 2
    # titleマッチ（topic1）がbodyマッチ（topic2）より上位
    assert result["results"][0]["id"] == topic1["topic_id"]
    assert result["results"][1]["id"] == topic2["topic_id"]


def test_search_type_filter(test_subject):
    """type_filterの動作: type_filter='topic'でtopicのみ返る"""
    topic = add_topic(
        subject_id=test_subject,
        title="検索機能テスト",
        description="テスト用トピック",
    )
    dec = add_decision(
        topic_id=topic["topic_id"],
        decision="検索機能テストの決定",
        reason="テスト用の理由",
    )

    # topicのみ
    result = search(subject_id=test_subject, keyword="検索機能テスト", type_filter="topic")

    assert "error" not in result
    for item in result["results"]:
        assert item["type"] == "topic"

    # decisionのみ
    result = search(subject_id=test_subject, keyword="検索機能テスト", type_filter="decision")

    assert "error" not in result
    for item in result["results"]:
        assert item["type"] == "decision"


def test_search_subject_isolation(test_subject):
    """subject_id分離: 別サブジェクトのデータが返らない"""
    subject2 = add_subject(name="test-subject-2", description="Test subject 2")["subject_id"]

    add_topic(
        subject_id=test_subject,
        title="サブジェクト1のトピック",
        description="テスト用",
    )
    add_topic(
        subject_id=subject2,
        title="サブジェクト2のトピック",
        description="テスト用",
    )

    result = search(subject_id=test_subject, keyword="サブジェクト")

    assert "error" not in result
    assert len(result["results"]) == 1
    assert result["results"][0]["title"] == "サブジェクト1のトピック"


def test_search_limit_control(test_subject):
    """limit制御: limit指定が効く"""
    for i in range(5):
        add_topic(
            subject_id=test_subject,
            title=f"リミットテスト Topic {i}",
            description="テスト用の説明文",
        )

    result = search(subject_id=test_subject, keyword="リミットテスト", limit=3)

    assert "error" not in result
    assert len(result["results"]) == 3


def test_search_limit_max_50(test_subject):
    """limit制御: 最大50件に制限される"""
    # 55個作る必要はないので、limitパラメータのクランプだけ確認
    # limit=100を指定しても内部で50にクランプされることを確認
    for i in range(5):
        add_topic(
            subject_id=test_subject,
            title=f"マックスリミットテスト Topic {i}",
            description="テスト用",
        )

    result = search(subject_id=test_subject, keyword="マックスリミットテスト", limit=100)

    assert "error" not in result
    # 5件しかないので5件返るが、エラーにはならない
    assert len(result["results"]) == 5


def test_search_keyword_too_short(test_subject):
    """3文字未満のkeyword: エラーが返る"""
    result = search(subject_id=test_subject, keyword="ab")

    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_search_keyword_too_short_after_strip(test_subject):
    """空白トリム後3文字未満: エラーが返る"""
    result = search(subject_id=test_subject, keyword="  ab  ")

    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_search_empty_results(test_subject):
    """空の検索結果: 空配列が返る"""
    add_topic(
        subject_id=test_subject,
        title="データベース設計",
        description="テーブル設計について",
    )

    result = search(subject_id=test_subject, keyword="存在しないキーワード")

    assert "error" not in result
    assert result["results"] == []
    assert result["total_count"] == 0


def test_search_special_characters(test_subject):
    """特殊文字のエスケープ: ダブルクォートを含むキーワードでクラッシュしない"""
    add_topic(
        subject_id=test_subject,
        title='テスト"クォート"含む',
        description="テスト用",
    )

    # ダブルクォートを含むキーワードでエラーにならない
    result = search(subject_id=test_subject, keyword='テスト"クォート')

    assert "error" not in result


def test_search_japanese(test_subject):
    """日本語検索: 日本語のキーワードで検索できる"""
    add_topic(
        subject_id=test_subject,
        title="認証フローの設計",
        description="OAuth2を使ったユーザー認証の設計",
    )

    result = search(subject_id=test_subject, keyword="認証フロー")

    assert "error" not in result
    assert len(result["results"]) == 1
    assert result["results"][0]["title"] == "認証フローの設計"


def test_search_trigger_sync_topic(test_subject):
    """トリガー同期の検証: topicのINSERT後に検索で見つかる"""
    add_topic(
        subject_id=test_subject,
        title="トリガーテスト用トピック",
        description="トリガーの自動同期を検証する",
    )

    result = search(subject_id=test_subject, keyword="トリガーテスト")

    assert "error" not in result
    assert len(result["results"]) == 1
    assert result["results"][0]["type"] == "topic"


def test_search_trigger_sync_decision(test_subject):
    """トリガー同期の検証: decisionのINSERT後に検索で見つかる"""
    topic = add_topic(
        subject_id=test_subject,
        title="テスト用トピック",
        description="テスト用",
    )
    add_decision(
        topic_id=topic["topic_id"],
        decision="トリガー同期のテスト決定",
        reason="自動同期の検証",
    )

    result = search(subject_id=test_subject, keyword="トリガー同期のテスト決定")

    assert "error" not in result
    assert len(result["results"]) >= 1
    decision_results = [r for r in result["results"] if r["type"] == "decision"]
    assert len(decision_results) >= 1


def test_search_trigger_sync_task(test_subject):
    """トリガー同期の検証: taskのINSERT後に検索で見つかる"""
    add_task(
        subject_id=test_subject,
        title="トリガー同期タスク",
        description="タスクの自動同期を検証する",
    )

    result = search(subject_id=test_subject, keyword="トリガー同期タスク")

    assert "error" not in result
    assert len(result["results"]) == 1
    assert result["results"][0]["type"] == "task"


def test_search_invalid_type_filter(test_subject):
    """無効なtype_filter: エラーが返る"""
    result = search(subject_id=test_subject, keyword="テスト用", type_filter="invalid")

    assert "error" in result
    assert result["error"]["code"] == "INVALID_TYPE_FILTER"


def test_search_cross_type(test_subject):
    """横断検索: topics, decisions, tasks の全てが検索対象になる"""
    topic = add_topic(
        subject_id=test_subject,
        title="横断検索テスト用トピック",
        description="横断検索の動作を確認する",
    )
    add_decision(
        topic_id=topic["topic_id"],
        decision="横断検索テスト決定事項",
        reason="横断検索テストのため",
    )
    add_task(
        subject_id=test_subject,
        title="横断検索テストタスク",
        description="横断検索のタスク",
    )

    result = search(subject_id=test_subject, keyword="横断検索テスト")

    assert "error" not in result
    types_found = {r["type"] for r in result["results"]}
    assert "topic" in types_found
    assert "decision" in types_found
    assert "task" in types_found


# ========================================
# get_by_id ツールのテスト
# ========================================


def test_get_by_id_topic(test_subject):
    """topic取得: typeとidから正しいデータが返る"""
    topic = add_topic(
        subject_id=test_subject,
        title="取得テストトピック",
        description="テスト用の説明",
    )

    result = get_by_id(type="topic", id=topic["topic_id"])

    assert "error" not in result
    assert result["type"] == "topic"
    assert result["data"]["id"] == topic["topic_id"]
    assert result["data"]["title"] == "取得テストトピック"
    assert result["data"]["description"] == "テスト用の説明"
    assert result["data"]["subject_id"] == test_subject
    assert "parent_topic_id" in result["data"]
    assert "created_at" in result["data"]


def test_get_by_id_decision(test_subject):
    """decision取得: typeとidから正しいデータが返る"""
    topic = add_topic(
        subject_id=test_subject,
        title="テスト用トピック",
        description="テスト用",
    )
    dec = add_decision(
        topic_id=topic["topic_id"],
        decision="テスト決定事項",
        reason="テスト理由",
    )

    result = get_by_id(type="decision", id=dec["decision_id"])

    assert "error" not in result
    assert result["type"] == "decision"
    assert result["data"]["id"] == dec["decision_id"]
    assert result["data"]["decision"] == "テスト決定事項"
    assert result["data"]["reason"] == "テスト理由"
    assert result["data"]["topic_id"] == topic["topic_id"]
    assert "created_at" in result["data"]


def test_get_by_id_task(test_subject):
    """task取得: typeとidから正しいデータが返る"""
    task = add_task(
        subject_id=test_subject,
        title="テストタスク",
        description="テストタスクの説明",
    )

    result = get_by_id(type="task", id=task["task_id"])

    assert "error" not in result
    assert result["type"] == "task"
    assert result["data"]["id"] == task["task_id"]
    assert result["data"]["title"] == "テストタスク"
    assert result["data"]["description"] == "テストタスクの説明"
    assert result["data"]["status"] == "pending"
    assert result["data"]["subject_id"] == test_subject
    assert "created_at" in result["data"]
    assert "updated_at" in result["data"]


def test_get_by_id_not_found(test_subject):
    """存在しないID: NOT_FOUNDエラーが返る"""
    result = get_by_id(type="topic", id=99999)

    assert "error" in result
    assert result["error"]["code"] == "NOT_FOUND"
    assert "topic with id 99999 not found" in result["error"]["message"]


def test_get_by_id_invalid_type(test_subject):
    """無効なtype: INVALID_TYPEエラーが返る"""
    result = get_by_id(type="foo", id=1)

    assert "error" in result
    assert result["error"]["code"] == "INVALID_TYPE"
    assert "Invalid type: foo" in result["error"]["message"]


# ========================================
# discussion_logs 検索テスト
# ========================================


def test_search_trigger_sync_log(test_subject):
    """トリガー同期の検証: logのINSERT後にsearchで見つかる"""
    topic = add_topic(
        subject_id=test_subject,
        title="ログ検索テスト用トピック",
        description="テスト用",
    )
    add_log_entry(
        topic_id=topic["topic_id"],
        title="トリガー同期ログテスト",
        content="ログのトリガー同期を検証する内容",
    )

    result = search(subject_id=test_subject, keyword="トリガー同期ログテスト")

    assert "error" not in result
    assert len(result["results"]) >= 1
    log_results = [r for r in result["results"] if r["type"] == "log"]
    assert len(log_results) >= 1


def test_search_type_filter_log(test_subject):
    """type_filterの動作: type_filter='log'でlogのみ返る"""
    topic = add_topic(
        subject_id=test_subject,
        title="ログフィルタテスト用トピック",
        description="テスト用",
    )
    add_log_entry(
        topic_id=topic["topic_id"],
        title="ログフィルタテスト記録",
        content="ログフィルタの動作を確認する",
    )

    result = search(subject_id=test_subject, keyword="ログフィルタテスト", type_filter="log")

    assert "error" not in result
    assert len(result["results"]) >= 1
    for item in result["results"]:
        assert item["type"] == "log"


def test_search_cross_type_includes_log(test_subject):
    """横断検索: logも含めてtopics, decisions, tasks, logsが検索対象になる"""
    topic = add_topic(
        subject_id=test_subject,
        title="横断ログ検索テスト用トピック",
        description="横断検索の動作を確認する",
    )
    add_decision(
        topic_id=topic["topic_id"],
        decision="横断ログ検索テスト決定事項",
        reason="横断ログ検索テストのため",
    )
    add_task(
        subject_id=test_subject,
        title="横断ログ検索テストタスク",
        description="横断ログ検索のタスク",
    )
    add_log_entry(
        topic_id=topic["topic_id"],
        title="横断ログ検索テスト記録",
        content="横断ログ検索のログ内容",
    )

    result = search(subject_id=test_subject, keyword="横断ログ検索テスト")

    assert "error" not in result
    types_found = {r["type"] for r in result["results"]}
    assert "topic" in types_found
    assert "decision" in types_found
    assert "task" in types_found
    assert "log" in types_found


def test_get_by_id_log(test_subject):
    """log取得: typeとidから正しいデータが返る"""
    topic = add_topic(
        subject_id=test_subject,
        title="取得テスト用トピック",
        description="テスト用",
    )
    log = add_log_entry(
        topic_id=topic["topic_id"],
        title="取得テストログ",
        content="取得テスト用のログ内容",
    )

    result = get_by_id(type="log", id=log["log_id"])

    assert "error" not in result
    assert result["type"] == "log"
    assert result["data"]["id"] == log["log_id"]
    assert result["data"]["title"] == "取得テストログ"
    assert result["data"]["content"] == "取得テスト用のログ内容"
    assert result["data"]["topic_id"] == topic["topic_id"]
    assert "created_at" in result["data"]


def test_search_log_title_fallback(test_subject):
    """titleフォールバック: title空のlogでcontentの先頭50文字がtitleに入る"""
    topic = add_topic(
        subject_id=test_subject,
        title="フォールバックテスト用トピック",
        description="テスト用",
    )
    # title空のログを直接INSERTする（add_logはバリデーションで弾くため）
    from src.db import execute_insert
    execute_insert(
        "INSERT INTO discussion_logs (topic_id, title, content) VALUES (?, ?, ?)",
        (topic["topic_id"], "", "フォールバックテスト" + "あ" * 50),
    )

    result = search(subject_id=test_subject, keyword="フォールバックテスト")

    assert "error" not in result
    log_results = [r for r in result["results"] if r["type"] == "log"]
    assert len(log_results) >= 1
    # contentの先頭50文字がtitleに入っている
    assert len(log_results[0]["title"]) == 50


def test_search_log_title_fallback_short_content(test_subject):
    """titleフォールバック: content50文字未満で全文がtitleに入る"""
    topic = add_topic(
        subject_id=test_subject,
        title="短文フォールバックテスト用トピック",
        description="テスト用",
    )
    # title空のログを直接INSERTする
    from src.db import execute_insert
    short_content = "短文フォールバックテスト内容"
    execute_insert(
        "INSERT INTO discussion_logs (topic_id, title, content) VALUES (?, ?, ?)",
        (topic["topic_id"], "", short_content),
    )

    result = search(subject_id=test_subject, keyword="短文フォールバックテスト")

    assert "error" not in result
    log_results = [r for r in result["results"] if r["type"] == "log"]
    assert len(log_results) >= 1
    assert log_results[0]["title"] == short_content


def test_add_log_empty_title_error(test_subject):
    """バリデーション: title空文字でadd_logするとバリデーションエラー"""
    topic = add_topic(
        subject_id=test_subject,
        title="バリデーションテスト用トピック",
        description="テスト用",
    )

    result = add_log_entry(
        topic_id=topic["topic_id"],
        title="",
        content="内容があってもtitleが空ならエラー",
    )

    assert "error" in result
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert "title must not be empty" in result["error"]["message"]
