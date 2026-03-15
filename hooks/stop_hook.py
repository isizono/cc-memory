"""Stop hook: イベント駆動アーキテクチャ Phase 1

処理フロー:
1. stdin読み込み → JSON parse
2. ブロック上限チェック（_BLOCK_LIMIT回で強制approve）
3. transcript差分読み → イベント抽出 → events.jsonl追記
4. events.jsonl全読み
5. Skill Span判定 → Span中なら即approve（安全弁: MAX_SKILL_SPAN_TURNS）
6. メタタグ判定（最新e:metaイベント、初期ターン猶予あり）
7. context retrieval判定（e:toolで取得系ツールが1件でもあるか）
8. check-in判定（e:toolでcheck_in/add_activityが1件でもあるか、猶予あり）
9. トピック変更 → 記録チェック（prev_topic比較 + 初回遷移許容 + e:toolで直近N turn）
10. nudge判定 + 状態更新 → approve
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
    _CHECKIN_TOOLS,
    _CONTEXT_RETRIEVAL_TOOLS,
    _RECORDING_TOOLS,
    extract_events,
    extract_last_activity_id,
    extract_text_from_entry,
    get_last_assistant_entry,
    parse_meta_tag,
    read_transcript_from_offset,
)

_BLOCK_LIMIT = 1
_CHECKIN_DEFER_TURNS = 2
_MAX_SKILL_SPAN_TURNS = 20
_NUDGE_INTERVAL = 2
_RECENT_TURNS_WINDOW = 3


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

        # 3. transcript差分読み → イベント抽出 → events.jsonl追記
        offset = state.get_transcript_offset()
        current_turn = state.get_current_turn()
        new_entries, new_offset, offset_was_reset = read_transcript_from_offset(transcript_path, offset)

        # オフセットリセット時はcurrent_turnとevents.jsonlもリセット
        if offset_was_reset:
            current_turn = 0
            # events.jsonlを空にする（古いイベントは無効）
            if state.events_path.exists():
                state.events_path.unlink()

        new_events, current_turn = extract_events(new_entries, current_turn)

        # stdinのlast_assistant_messageからメタタグを補完（レースコンディション対策）
        last_msg = data.get("last_assistant_message", "")
        if last_msg:
            meta = parse_meta_tag(last_msg)
            if meta:
                # 同一turnで同じtopicのmetaイベントがなければ追加
                has_same_meta = any(
                    e["e"] == "meta" and e["turn"] == current_turn and e["topic"] == meta["topic_name"]
                    for e in new_events
                )
                if not has_same_meta:
                    new_events.append({
                        "e": "meta",
                        "topic": meta["topic_name"],
                        "turn": current_turn,
                    })

        state.append_events(new_events)
        state.set_transcript_offset(new_offset)
        state.set_current_turn(current_turn)

        # 4. events.jsonl全読み
        all_events = state.read_events()

        # 5. Skill Span判定
        in_skill_span = _is_in_skill_span(all_events, current_turn)
        if in_skill_span:
            state.reset_block_count()
            _output("approve", "Skill Span中のためチェックをスキップします。")
            _update_state_on_approve(state, all_events, transcript_path)
            return

        # 6. メタタグ判定
        latest_meta = _get_latest_meta(all_events)
        if latest_meta is None:
            if current_turn <= 1:
                state.reset_block_count()
                _output("approve", "1ターン目のためメタタグチェックを猶予します。")
                _update_state_on_approve(state, all_events, transcript_path)
                return
            # フォールバック: last_assistant_messageが無い場合transcriptから取得
            # NOTE: get_last_assistant_entryは全行逆順走査（Phase 3で廃止予定）
            if not last_msg:
                last_entry = get_last_assistant_entry(transcript_path)
                if last_entry is not None:
                    text = extract_text_from_entry(last_entry)
                    meta_fb = parse_meta_tag(text)
                    if meta_fb:
                        latest_meta = meta_fb["topic_name"]
            if latest_meta is None:
                state.increment_block_count()
                _output(
                    "block",
                    "応答の最後にメタタグを出力してください。フォーマット: "
                    "<!-- [meta] topic: xxx -->",
                )
                return

        current_topic_name = latest_meta

        # 7. context retrieval判定
        has_retrieval = any(
            e["e"] == "tool" and e.get("name") in _CONTEXT_RETRIEVAL_TOOLS
            for e in all_events
        )
        if not has_retrieval:
            state.increment_block_count()
            _output(
                "block",
                "応答の前に過去のコンテキストを取得してください。"
                "search / get_topics / get_decisions / get_logs / get_activities / get_by_ids "
                "のいずれかを使ってください。",
            )
            return

        # 8. check-in判定
        has_checkin = any(
            e["e"] == "tool" and e.get("name") in _CHECKIN_TOOLS
            for e in all_events
        )
        if has_checkin:
            # activity_idを抽出して保存
            _update_checked_in_activity(state, all_events, transcript_path)
        elif current_turn == _CHECKIN_DEFER_TURNS:
            # one-shot block: 正確にdefer turnで1回だけblock
            state.increment_block_count()
            _output(
                "block",
                "アクティビティにcheck-inしてください。"
                "該当するものがなければadd_activityで作成してください。",
            )
            return

        # 9. トピック変更チェック
        prev_topic = state.get_prev_topic()
        if prev_topic is not None and prev_topic != current_topic_name:
            # 初回遷移判定: 過去turnのmetaイベントに異なるtopicが2つ以上あるか
            # current_turnのmetaは「今まさに遷移した」ものなので除外
            prev_meta_topics = {
                e["topic"] for e in all_events
                if e["e"] == "meta" and e.get("turn", 0) < current_turn
            }
            is_first_transition = len(prev_meta_topics) <= 1
            if not is_first_transition:
                # 直近N turnに記録ツール呼び出しがあるか
                recent_turn_threshold = current_turn - _RECENT_TURNS_WINDOW
                has_recent_record = any(
                    e["e"] == "tool"
                    and e.get("name") in _RECORDING_TOOLS
                    and e.get("turn", 0) >= recent_turn_threshold
                    for e in all_events
                )
                if not has_recent_record:
                    state.increment_block_count()
                    _output(
                        "block",
                        "トピックが変わりました。移動前に記録（add_decision / add_log / add_topic）を"
                        "行ってください。",
                    )
                    return

        # 10. nudge判定 + 状態更新 + approve
        state.reset_block_count()
        _output("approve")
        _update_state_on_approve(state, all_events, transcript_path)
        _handle_nudges(state, all_events, current_turn)

    except Exception as e:
        # フェイルオープン: 例外時はapprove
        print(f"stop_hook.py error: {e}", file=sys.stderr)
        _output("approve", f"stop_hook.py internal error: {e}")


def _is_in_skill_span(events: list[dict], current_turn: int) -> bool:
    """Skill Span中かどうかを判定する。

    最後のskillイベントのturnから現在のturnまでの距離が
    MAX_SKILL_SPAN_TURNS以内で、かつ直近のturnにskillイベントがある場合。
    """
    last_skill_turn = None
    for e in events:
        if e["e"] == "skill":
            last_skill_turn = e.get("turn", 0)

    if last_skill_turn is None:
        return False

    # 安全弁: MAX_SKILL_SPAN_TURNS超過で強制終了
    if current_turn - last_skill_turn > _MAX_SKILL_SPAN_TURNS:
        return False

    # 直近turnにskillイベントがあるか（= skillイベントがないturnが来たらSpan終了）
    return last_skill_turn >= current_turn


def _get_latest_meta(events: list[dict]) -> str | None:
    """events内の最新metaイベントからtopic名を取得する。"""
    for e in reversed(events):
        if e["e"] == "meta":
            return e.get("topic")
    return None


def _update_checked_in_activity(
    state: HookState, events: list[dict], transcript_path: str
) -> None:
    """check_inイベントからactivity_idを抽出し、checked_in_activityを更新する。"""
    # check_inイベントからactivity_idを取得
    for e in reversed(events):
        if e["e"] == "tool" and e.get("name") == "check_in" and "activity_id" in e:
            state.set_checked_in_activity(e["activity_id"])
            return

    # フォールバック: transcript全走査（add_activityのtool_result対応）
    aid = extract_last_activity_id(transcript_path)
    if aid is not None:
        state.set_checked_in_activity(aid)


def _update_state_on_approve(
    state: HookState, events: list[dict], transcript_path: str
) -> None:
    """approve時の状態更新（prev_topic, heartbeat）"""
    # prev_topic更新
    latest_meta = _get_latest_meta(events)
    if latest_meta:
        state.set_prev_topic(latest_meta)

    # heartbeat更新
    activity_id = state.get_checked_in_activity()
    if activity_id is not None:
        update_heartbeat(activity_id)


def _handle_nudges(state: HookState, events: list[dict], current_turn: int) -> None:
    """nudge判定: events.jsonlから直接判定してnudgeイベントを追記する。

    pretooluse_hookがevents.jsonlを読んでnudge注入を判定するため、
    nudgeフラグの代わりにnudgeイベントをevents.jsonlに追記する。
    """
    nudge_events: list[dict] = []

    # record nudge: _NUDGE_INTERVAL turnごとに記録がなければ発火
    if current_turn > 0 and current_turn % _NUDGE_INTERVAL == 0:
        recent_turn_threshold = current_turn - _NUDGE_INTERVAL
        has_recent_record = any(
            e["e"] == "tool"
            and e.get("name") in _RECORDING_TOOLS
            and e.get("turn", 0) > recent_turn_threshold
            for e in events
        )
        if not has_recent_record:
            nudge_events.append({
                "e": "nudge",
                "type": "record",
                "turn": current_turn,
            })

    # activity nudge: 直近turnにadd_decisionあり & add_activity/check_inなし
    recent_events = [e for e in events if e.get("turn", 0) == current_turn]
    has_decision = any(
        e["e"] == "tool" and e.get("name") == "add_decision" for e in recent_events
    )
    if has_decision:
        has_activity = any(
            e["e"] == "tool" and e.get("name") in _CHECKIN_TOOLS for e in recent_events
        )
        if not has_activity:
            nudge_events.append({
                "e": "nudge",
                "type": "activity",
                "turn": current_turn,
            })

    if nudge_events:
        state.append_events(nudge_events)


if __name__ == "__main__":
    main()
