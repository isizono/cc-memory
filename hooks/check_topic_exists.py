#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
指定topic_idがDBに存在し、topic名が一致するかチェックするスクリプト。
Stopフックからメタタグparse後に呼び出される。

Usage:
    python check_topic_exists.py <topic_id> [<topic_name>]

Returns (JSON):
    {"exists": true, "name_match": true}
    {"exists": true, "name_match": false, "actual_name": "..."}
    {"exists": false}
"""
import json
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from src.db import execute_query


def main():
    # 引数がない場合は「存在しない」として正常終了
    if len(sys.argv) < 2:
        print(json.dumps({"exists": False}))
        sys.exit(0)

    try:
        topic_id = int(sys.argv[1])
    except ValueError:
        # 不正な引数はエラー（exit 1）
        print(f"Error: Invalid topic_id: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

    topic_name = sys.argv[2] if len(sys.argv) >= 3 else None

    try:
        rows = execute_query(
            "SELECT id, title FROM discussion_topics WHERE id = ?",
            (topic_id,),
        )
        if len(rows) == 0:
            print(json.dumps({"exists": False}))
            return

        actual_name = rows[0]["title"]

        if topic_name is None:
            # topic名が渡されなかった場合は存在チェックのみ
            print(json.dumps({"exists": True}))
        elif topic_name == actual_name:
            print(json.dumps({"exists": True, "name_match": True}))
        else:
            print(json.dumps({"exists": True, "name_match": False, "actual_name": actual_name}))
    except Exception as e:
        # DBエラーはエラー（exit 1）
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
