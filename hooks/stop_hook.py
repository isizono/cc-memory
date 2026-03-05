"""Stop hook: メタタグ強制 + トピック管理 + nudgeカウンター

stop_enforce_metatag.sh と stop_nudge_record.sh を統合した Python 実装。
共通モジュール（hook_state, hook_topic, hook_transcript）を利用する。

処理フロー:
1. stdin読み込み → JSON parse
2. ブロック上限チェック（3回で強制approve）
3. メタタグparse（一次ソース: stdinのlast_assistant_message、フォールバック: transcript）
4. トピック存在チェック
5. トピック名一致チェック
6. トピック変更チェック（前topicに記録があるか）
7. nudgeカウンター管理
8. 状態更新 → approve
"""
import json
import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加（src.db等の参照用）
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from hooks.hook_state import HookState
from hooks.hook_topic import check_topic_exists
from hooks.hook_transcript import (
    extract_text_from_entry,
    find_tool_calls_for_topic,
    get_assistant_entries,
    get_last_assistant_entry,
    has_recent_recording,
    parse_meta_tag,
)

_BLOCK_LIMIT = 3
_NUDGE_INTERVAL = 3
_FIRST_TOPIC_ID = 1


def _output(decision: str, reason: str = "") -> None:
    result = {"decision": decision}
    if reason:
        result["reason"] = reason
    print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    try:
        # 環境変数によるテスト用オーバーライド
        if os.environ.get("HOOK_STATE_DIR"):
            HookState.BASE_DIR = Path(os.environ["HOOK_STATE_DIR"])

        # 1. stdin読み込み
        raw = sys.stdin.read()
        data = json.loads(raw)
        transcript_path = data.get("transcript_path", "")
        session_id = data.get("session_id", "")

        if not session_id:
            _output("approve", "session_id is empty")
            return

        state = HookState(session_id)

        # 2. ブロック上限チェック
        if state.get_block_count() >= _BLOCK_LIMIT:
            state.reset_block_count()
            _output("approve", "ブロック上限（3回）に達しました。強制的に通します。")
            return

        # 3. メタタグparse
        # 一次ソース: stdinのlast_assistant_message
        last_msg = data.get("last_assistant_message", "")
        meta = parse_meta_tag(last_msg) if last_msg else None

        # フォールバック: transcriptから取得
        if meta is None:
            last_entry = get_last_assistant_entry(transcript_path)
            if last_entry is not None:
                text = extract_text_from_entry(last_entry)
                meta = parse_meta_tag(text)

        if meta is None:
            state.increment_block_count()
            _output(
                "block",
                "応答の最初にメタタグを出力してください。フォーマット: "
                "<!-- [meta] subject: xxx (id: N) | topic: yyy (id: M) -->",
            )
            return

        current_topic_id = meta["topic_id"]
        current_topic_name = meta["topic_name"]

        # 4. トピック存在チェック
        topic_result = check_topic_exists(current_topic_id, current_topic_name)

        if not topic_result["exists"]:
            state.increment_block_count()
            _output(
                "block",
                f"topic_id={current_topic_id} は存在しません。"
                "get_topics で正しいtopic_idを確認してください",
            )
            return

        # 5. トピック名一致チェック（blockしない）
        if topic_result.get("name_match") is False:
            actual_name = topic_result.get("actual_name", "")
            state.set_nudge_topic_name(current_topic_id, actual_name)

        # 6. トピック変更チェック
        prev_topic = state.get_prev_topic()

        if (
            prev_topic is not None
            and prev_topic != current_topic_id
            and prev_topic != _FIRST_TOPIC_ID
        ):
            entries = get_assistant_entries(transcript_path)
            if not find_tool_calls_for_topic(entries, prev_topic):
                state.increment_block_count()
                _output(
                    "block",
                    f"トピックが変わりました。前のトピック(id={prev_topic})に"
                    "決定事項(add_decision)またはログ(add_log)を記録してから移動してください",
                )
                return

        # 7. nudgeカウンター
        nudge_count = state.increment_nudge_counter()

        if nudge_count % _NUDGE_INTERVAL == 0:
            recent_entries = get_assistant_entries(transcript_path, last_n=10)
            if has_recent_recording(recent_entries):
                state.reset_nudge_counter()
            else:
                state.set_nudge_pending()

        # 8. 状態更新 + approve
        state.set_prev_topic(current_topic_id)
        state.reset_block_count()
        _output("approve")

    except Exception as e:
        # フェイルオープン: 例外時はapprove
        print(f"stop_hook.py error: {e}", file=sys.stderr)
        _output("approve", f"stop_hook.py internal error: {e}")


if __name__ == "__main__":
    main()
