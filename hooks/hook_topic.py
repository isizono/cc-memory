"""hook共通: トピックDB操作

check_topic_exists.py のCLIロジックを関数化。
src.db.execute_query に依存。
"""
import sys
from pathlib import Path


def check_topic_exists(topic_id: int, topic_name: str | None = None) -> dict:
    """指定topic_idがDBに存在し、topic名が一致するかチェックする。

    Returns:
        {"exists": True, "name_match": True} - 存在し名前一致
        {"exists": True, "name_match": False, "actual_name": "..."} - 存在するが名前不一致
        {"exists": True} - 存在（名前チェックなし）
        {"exists": False} - 存在しない

    Raises:
        sqlite3.Error等 - DB接続失敗時（呼び出し元でcatch）
    """
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from src.db import execute_query

    rows = execute_query(
        "SELECT id, title FROM discussion_topics WHERE id = ?",
        (topic_id,),
    )
    if len(rows) == 0:
        return {"exists": False}

    actual_name = rows[0]["title"]

    if topic_name is None:
        return {"exists": True}
    elif topic_name == actual_name:
        return {"exists": True, "name_match": True}
    else:
        return {"exists": True, "name_match": False, "actual_name": actual_name}
