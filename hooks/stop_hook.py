"""Stop hook: メタタグ強制 + コンテキスト取得チェック + activity check-in + 記録強制 + nudgeカウンター

処理フロー:
1. stdin読み込み → JSON parse
2. ブロック上限チェック（1回で強制approve）
3. スキルスキップ判定（<command-name>検出 or 残りターン → 即approve）
4. メタタグparse（一次ソース: stdinのlast_assistant_message、フォールバック: transcript）
   → 1ターン目（approved_turns == 0）は猶予。2ターン目以降はなければblock
5. get系API呼び出しチェック（セッション中1回以上）
   → なければblock
6. activity check-inチェック（_CHECKIN_DEFER_TURNSターン後、one-shot block）
   → check-in/add_activity未呼出 → block
7. トピック変更チェック → 初回遷移は許容、2回目以降は記録がなければblock
8. nudgeカウンター管理
9. 状態更新 → approve
"""
import json
import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加（src.db等の参照用）
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from hooks.heartbeat import update_heartbeat
from hooks.hook_state import HookState
from hooks.hook_transcript import (
    extract_checkin_activity_id,
    extract_last_activity_id,
    extract_text_from_entry,
    get_last_assistant_entry,
    get_transcript_info,
    has_activity_checkin_calls,
    has_context_retrieval_calls,
    has_decision_without_activity,
    has_recent_recording,
    parse_meta_tag,
)

_BLOCK_LIMIT = 1
_NUDGE_INTERVAL = 2
_CHECKIN_DEFER_TURNS = 2
_SKILL_SKIP_TURNS = 3


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
            _output("approve", f"ブロック上限（{_BLOCK_LIMIT}回）に達しました。強制的に通します。")
            return

        # 3. スキルスキップ判定
        skill_skip = state.get_skill_skip_remaining()
        if skill_skip > 0:
            state.set_skill_skip_remaining(skill_skip - 1)
            state.reset_block_count()
            state.increment_approved_turns()
            _output("approve", f"スキル実行中（残り{skill_skip - 1}ターン）")
            return

        all_entries, has_skill_cmd = get_transcript_info(transcript_path)
        if has_skill_cmd:
            state.set_skill_skip_remaining(_SKILL_SKIP_TURNS - 1)
            state.reset_block_count()
            state.increment_approved_turns()
            _output("approve", "スキル実行を検出。チェックをスキップします。")
            return

        # 4. メタタグparse
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
            # 1ターン目（approved_turns == 0）はメタタグなしでも猶予
            if state.get_approved_turns() == 0:
                # メタタグなしでもapproveして次に進む（ステップ5以降はスキップ）
                state.reset_block_count()
                state.increment_approved_turns()
                _output("approve", "1ターン目のためメタタグチェックを猶予します。")
                return
            state.increment_block_count()
            _output(
                "block",
                "応答の最後にメタタグを出力してください。フォーマット: "
                "<!-- [meta] topic: xxx -->",
            )
            return

        current_topic_name = meta["topic_name"]

        # 5. get系API呼び出しチェック（セッション中1回以上）

        if not state.has_context_retrieval():
            if has_context_retrieval_calls(all_entries):
                state.set_context_retrieved()
            else:
                state.increment_block_count()
                _output(
                    "block",
                    "応答の前に過去のコンテキストを取得してください。"
                    "search / get_topics / get_decisions / get_logs / get_activities / get_by_ids "
                    "のいずれかを使ってください。",
                )
                return

        # 6. Activity check-in チェック（2ターン目）
        if not state.has_activity_checkin():
            has_checkin = has_activity_checkin_calls(all_entries)
            if has_checkin:
                state.set_activity_checkin()
                activity_id = extract_checkin_activity_id(all_entries)
                if activity_id is None:
                    activity_id = extract_last_activity_id(transcript_path)
                if activity_id is not None:
                    state.set_checked_in_activity(activity_id)
            elif state.get_approved_turns() >= _CHECKIN_DEFER_TURNS:
                state.set_activity_checkin()  # one-shot: 次回はスキップ
                state.increment_block_count()
                _output(
                    "block",
                    "アクティビティにcheck-inしてください。"
                    "該当するものがなければadd_activityで作成してください。",
                )
                return

        # 7. トピック変更チェック → 記録がなければblock（初回遷移は許容）
        prev_topic = state.get_prev_topic()
        if prev_topic is not None and prev_topic != current_topic_name:
            if not state.has_topic_transitioned():
                # 初回のトピック遷移は許容（check-in→実トピックへの着地等）
                state.set_topic_transitioned()
            else:
                recent_entries = all_entries[-5:] if all_entries else []
                if not has_recent_recording(recent_entries):
                    state.increment_block_count()
                    _output(
                        "block",
                        "トピックが変わりました。移動前に記録（add_decision / add_log / add_topic）を"
                        "行ってください。",
                    )
                    return

        # 8. nudgeカウンター
        nudge_count = state.increment_nudge_counter()

        if nudge_count % _NUDGE_INTERVAL == 0:
            recent_entries = all_entries[-10:] if all_entries else []
            if has_recent_recording(recent_entries):
                state.reset_nudge_counter()
            else:
                state.set_nudge_pending()

        # 8b. decision後のアクティビティ作成nudge
        recent_entries_short = all_entries[-2:] if all_entries else []
        if has_decision_without_activity(recent_entries_short):
            state.set_activity_nudge_pending()

        # 9. 状態更新 + approve
        activity_id = state.get_checked_in_activity()
        if activity_id is not None:
            update_heartbeat(activity_id)

        state.set_prev_topic(current_topic_name)
        state.reset_block_count()
        state.increment_approved_turns()
        _output("approve")

    except Exception as e:
        # フェイルオープン: 例外時はapprove
        print(f"stop_hook.py error: {e}", file=sys.stderr)
        _output("approve", f"stop_hook.py internal error: {e}")


if __name__ == "__main__":
    main()
