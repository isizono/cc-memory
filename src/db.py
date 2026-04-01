"""データベース接続と初期化を管理するモジュール"""
import sqlite3
import os
import logging
from pathlib import Path

import sqlite_vec
from yoyo import read_migrations
from yoyo import default_migration_table
from yoyo.backends import SQLiteBackend
from yoyo.connections import parse_uri

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def get_db_path() -> str:
    """データベースファイルのパスを取得する"""
    from src.config import DB_PATH

    # config.pyのDB_PATH（モジュールインポート時に解決済み）を優先
    if DB_PATH:
        return DB_PATH

    # 実行時の環境変数もチェック（テスト互換: テスト中に動的設定されるケース）
    db_path = os.environ.get("CCM_DB_PATH") or os.environ.get("DISCUSSION_DB_PATH")
    if db_path:
        return db_path

    # デフォルトは ~/.claude/.claude-code-memory/discussion.db
    home = Path.home()
    db_dir = home / ".claude" / ".claude-code-memory"
    db_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(db_dir / "discussion.db")


def verify_sqlite_vec() -> None:
    """sqlite-vec拡張が利用可能か起動時にチェックする。

    失敗時はわかりやすいエラーメッセージを出力してSystemExitを送出する。
    サーバー起動前（init_database前）に呼ぶこと。
    """
    conn = sqlite3.connect(":memory:")
    try:
        # Step 1: enable_load_extensionの有無チェック（パターンA: pyenv問題）
        if not hasattr(conn, "enable_load_extension"):
            logger.error(
                "sqlite-vec startup check failed: "
                "sqlite3.Connection.enable_load_extension() is not available.\n"
                "Your Python was built without --enable-loadable-sqlite-extensions.\n"
                "Fix: use Homebrew Python or rebuild with the flag.\n"
                "  brew install python@3.12\n"
                "  UV_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12 uv sync"
            )
            raise SystemExit(1)

        try:
            conn.enable_load_extension(True)
        except AttributeError:
            logger.error(
                "sqlite-vec startup check failed: "
                "enable_load_extension() exists but is not callable.\n"
                "Your Python was built without --enable-loadable-sqlite-extensions.\n"
                "Fix: use Homebrew Python or rebuild with the flag.\n"
                "  brew install python@3.12\n"
                "  UV_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12 uv sync"
            )
            raise SystemExit(1)

        # Step 2: sqlite_vec.load()の成否チェック（パターンB: ネイティブ拡張非互換）
        try:
            sqlite_vec.load(conn)
        except Exception as e:
            logger.error(
                "sqlite-vec startup check failed: "
                f"native extension could not be loaded: {e}\n"
                "The sqlite-vec binary is incompatible with your environment.\n"
                "Fix: reinstall sqlite-vec or use a compatible Python build.\n"
                "  UV_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12 uv sync"
            )
            raise SystemExit(1)
        finally:
            conn.enable_load_extension(False)
    finally:
        conn.close()

    logger.info("sqlite-vec startup check passed")


def get_connection() -> sqlite3.Connection:
    """データベース接続を取得する"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 辞書ライクなアクセスを可能にする
    conn.execute("PRAGMA foreign_keys = ON")  # 外部キー制約を有効化
    try:
        _load_sqlite_vec(conn)
    except Exception:
        logger.warning("sqlite-vec could not be loaded. Vector search will be unavailable.")
    return conn


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """sqlite-vec拡張をコネクションにロードする"""
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


class _VecSQLiteBackend(SQLiteBackend):
    """sqlite-vec拡張をロードするSQLiteBackend

    yoyoの内部API（SQLiteBackend, parse_uri, default_migration_table）に依存。
    pyproject.tomlでyoyo-migrationsのメジャーバージョンをピン留めすること。
    """

    def connect(self, dburi) -> sqlite3.Connection:
        conn = super().connect(dburi)
        try:
            _load_sqlite_vec(conn)
        except Exception:
            logger.warning("sqlite-vec could not be loaded. Vector search will be unavailable.")
        return conn


def _apply_migrations() -> None:
    """yoyoマイグレーションを適用する"""
    db_path = get_db_path()
    parsed = parse_uri(f"sqlite:///{db_path}")
    backend = _VecSQLiteBackend(parsed, default_migration_table)
    backend.init_database()
    migrations = read_migrations(str(MIGRATIONS_DIR))

    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))


def init_database() -> None:
    """データベースを初期化する（マイグレーション適用と初期データ投入）"""
    _apply_migrations()

    conn = get_connection()
    try:
        # 初期データの投入（subjects廃止後はタグベースで初期トピックを作成）
        # discussion_topicsにはtitleのUNIQUE制約がないため、存在確認してから挿入
        cursor = conn.execute(
            "SELECT id FROM discussion_topics WHERE title = 'first_topic'"
        )
        if cursor.fetchone() is None:
            conn.execute(
                """
                INSERT INTO discussion_topics (title, description)
                VALUES ('first_topic', 'これはサンプルのトピックです。トピックは「この会話を一言で表すと？」に答えられる粒度が目安です。例：「[議論] ユーザー認証に使う外部サービスの選定」「[設計] エラーAPIのレスポンス形式」「[作業] 商品詳細→カート画面遷移時のクラッシュ」など。新しい話題が出てきたら、新しいトピックを作成してください。')
                """
            )
            topic_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            # 初期トピックにdomain:defaultタグを付与
            from src.services.tag_service import ensure_tag_ids, link_tags
            tag_ids = ensure_tag_ids(conn, [("domain", "default")])
            link_tags(conn, "topic_tags", "topic_id", topic_id, tag_ids)

        # FTS5初期マイグレーション
        _migrate_fts5_search_index(conn)

        conn.commit()
    finally:
        conn.close()


def _check_fts5_available(conn: sqlite3.Connection) -> bool:
    """FTS5が利用可能か確認する"""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_check USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_check")
        return True
    except sqlite3.OperationalError:
        return False


def _migrate_fts5_search_index(conn: sqlite3.Connection) -> None:
    """FTS5検索インデックスの初期データマイグレーション（contentless方式）"""
    if not _check_fts5_available(conn):
        logger.warning("FTS5 is not available. Skipping search index migration.")
        return

    # search_indexが空の場合のみ実行
    cursor = conn.execute("SELECT COUNT(*) FROM search_index")
    if cursor.fetchone()[0] > 0:
        return  # 既にデータがある場合はスキップ

    # topics
    conn.execute("""
        INSERT OR IGNORE INTO search_index (source_type, source_id, title)
        SELECT 'topic', id, title
        FROM discussion_topics
    """)

    # decisions（topic_idは常にNOT NULL）
    conn.execute("""
        INSERT OR IGNORE INTO search_index (source_type, source_id, title)
        SELECT 'decision', d.id, d.decision
        FROM decisions d
    """)

    # activities
    conn.execute("""
        INSERT OR IGNORE INTO search_index (source_type, source_id, title)
        SELECT 'activity', id, title
        FROM activities
    """)

    # materials
    conn.execute("""
        INSERT OR IGNORE INTO search_index (source_type, source_id, title)
        SELECT 'material', id, title
        FROM materials
    """)

    # FTS5インデックスにデータを投入（contentless方式ではrebuildが使えない）
    conn.execute("""
        INSERT INTO search_index_fts (rowid, title, body)
        SELECT si.id, si.title,
          COALESCE(
            CASE si.source_type
              WHEN 'topic' THEN (SELECT description FROM discussion_topics WHERE id = si.source_id)
              WHEN 'decision' THEN (SELECT reason FROM decisions WHERE id = si.source_id)
              WHEN 'activity' THEN (SELECT description FROM activities WHERE id = si.source_id)
              WHEN 'material' THEN (SELECT content FROM materials WHERE id = si.source_id)
            END,
            ''
          )
        FROM search_index si
    """)


def execute_query(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    """SELECT クエリを実行して結果を返す"""
    conn = get_connection()
    try:
        cursor = conn.execute(query, params)
        return cursor.fetchall()
    except sqlite3.Error as e:
        raise sqlite3.Error(f"クエリ実行エラー: {e}") from e
    finally:
        conn.close()


def execute_insert(query: str, params: tuple = ()) -> int:
    """INSERT クエリを実行して新しいIDを返す"""
    conn = get_connection()
    try:
        cursor = conn.execute(query, params)
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        raise
    except sqlite3.Error as e:
        conn.rollback()
        raise sqlite3.Error(f"INSERT実行エラー: {e}") from e
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    """sqlite3.Row を辞書に変換する"""
    return dict(row)
