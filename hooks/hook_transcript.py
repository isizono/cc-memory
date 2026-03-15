"""hook共通: transcript解析ユーティリティ

イベント駆動アーキテクチャ用の差分読み・イベント抽出と、
レガシー関数（Phase 3で廃止予定）を含む。
"""
import json
import re
from pathlib import Path

# --- cc-memory MCPツールのプレフィックス ---

_CC_MEMORY_PREFIX = "mcp__plugin_claude-code-memory_cc-memory__"

# --- コンテキスト取得ツール ---

_CONTEXT_RETRIEVAL_TOOLS = {
    "search",
    "get_topics",
    "get_decisions",
    "get_logs",
    "get_activities",
    "get_by_ids",
}

# --- 記録ツール ---

_RECORDING_TOOLS = {
    "add_decision",
    "add_topic",
    "add_log",
}

# --- check-in系ツール ---

_CHECKIN_TOOLS = {
    "check_in",
    "add_activity",
}


# ===================================================================
# イベント駆動アーキテクチャ: 差分読み + イベント抽出
# ===================================================================


def read_transcript_from_offset(transcript_path: str, offset: int) -> tuple[list[dict], int, bool]:
    """transcriptをバイトオフセットから読み、(新規エントリ一覧, 新オフセット, リセット発生)を返す。

    transcriptはappend-onlyのJSONL形式。offsetがファイルサイズを超えた場合は
    0にリセットして全読みする（defensive coding）。
    リセット発生時は呼び出し側でcurrent_turnやevents.jsonlもリセットすべき。
    """
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return [], 0, False

    try:
        file_size = path.stat().st_size
        offset_reset = offset > file_size
        if offset_reset:
            offset = 0

        entries = []
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
            new_offset = offset + len(data)

        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        return entries, new_offset, offset_reset

    except Exception:
        return [], offset, False


def is_user_message(entry: dict) -> bool:
    """エントリがUser Message（ターン境界）かどうかを判定する。

    User Message = ユーザーが実際に送信したuserエントリ。
    tool_resultやsystem-reminderを含むuser/humanエントリは除外する。
    string形式のsystem-reminderの誤判定は許容（発生率0.02%）。
    """
    if entry.get("type") not in ("user", "human"):
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
    return False


def extract_events(entries: list[dict], current_turn: int) -> tuple[list[dict], int]:
    """transcriptエントリ群からイベントを抽出する。

    3型イベントを抽出:
    - tool: cc-memoryツール呼び出し（assistantのtool_useブロック）
    - meta: メタタグ検出（assistantのtextブロック）
    - skill: スキル開始検出（User Messageの<command-name>タグ）

    Args:
        entries: transcriptの新規エントリ一覧
        current_turn: 現在のturn番号

    Returns:
        (抽出されたイベントのリスト, 更新後のturn番号)
    """
    events: list[dict] = []

    for entry in entries:
        entry_type = entry.get("type", "")

        # Turn境界検出: User Message到着で新turnが始まる
        if is_user_message(entry):
            current_turn += 1
            # skillイベント: User Messageに<command-name>が含まれる場合
            text = _extract_user_content_text(entry)
            match = re.search(r"<command-name>/?(.*?)</command-name>", text)
            if match:
                events.append({
                    "e": "skill",
                    "name": match.group(1).strip(),
                    "turn": current_turn,
                })
            continue

        # assistantエントリからtool/metaイベントを抽出
        if entry_type == "assistant":
            message = entry.get("message", {})
            content = message.get("content", [])

            if isinstance(content, str):
                meta = parse_meta_tag(content)
                if meta:
                    events.append({
                        "e": "meta",
                        "topic": meta["topic_name"],
                        "turn": current_turn,
                    })
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")

                if block_type == "tool_use":
                    name = block.get("name", "")
                    if name.startswith(_CC_MEMORY_PREFIX):
                        short_name = name[len(_CC_MEMORY_PREFIX):]
                        event: dict = {
                            "e": "tool",
                            "name": short_name,
                            "turn": current_turn,
                        }
                        # check_inのactivity_idを保存
                        if short_name == "check_in":
                            aid = block.get("input", {}).get("activity_id")
                            if aid is not None:
                                try:
                                    event["activity_id"] = int(aid)
                                except (ValueError, TypeError):
                                    pass
                        events.append(event)

                elif block_type == "text":
                    text = block.get("text", "")
                    meta = parse_meta_tag(text)
                    if meta:
                        events.append({
                            "e": "meta",
                            "topic": meta["topic_name"],
                            "turn": current_turn,
                        })

    return events, current_turn


# ===================================================================
# 共通ユーティリティ（イベント駆動でも使用）
# ===================================================================


def parse_meta_tag(text: str) -> dict | None:
    """テキストからメタタグをパースする。

    フォーマット:
    <!-- [meta] topic: xxx -->

    Returns:
        {"found": True, "topic_name": ...}
        or None
    """
    pattern = r'<!--\s*\[meta\]\s*topic:\s*(.+?)\s*-->'
    match = re.search(pattern, text)

    if match:
        return {
            "found": True,
            "topic_name": match.group(1).strip(),
        }

    return None


def get_last_assistant_entry(transcript_path: str) -> dict | None:
    """transcriptから最後のassistantエントリ（textブロック含む）を取得する。
    全行を読み、逆順でtextブロックを含む最初のassistantエントリを返す。"""
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return None

    try:
        with open(path) as f:
            lines = f.readlines()
    except Exception:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") == "assistant" and _has_text_block(entry):
                return entry
        except json.JSONDecodeError:
            continue

    return None


def extract_text_from_entry(entry: dict) -> str:
    """エントリからテキスト内容を抽出する。"""
    message = entry.get("message", {})
    content = message.get("content", [])

    if isinstance(content, str):
        return content

    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))
        elif isinstance(block, str):
            texts.append(block)

    return "\n".join(texts)


def _has_text_block(entry: dict) -> bool:
    """エントリにtextブロックが含まれるかチェック。"""
    content = entry.get("message", {}).get("content", [])
    if isinstance(content, str):
        return bool(content.strip())
    return any(
        isinstance(block, dict) and block.get("type") == "text"
        for block in content
    )


def _extract_user_content_text(entry: dict) -> str:
    """userエントリからcontent文字列を取得する。"""
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        )
    return ""


# ===================================================================
# レガシー関数（Phase 3で廃止予定）
# 現在はテストからの参照があるため残置
# ===================================================================


_RECORDING_TOOLS_FULL = [
    f"{_CC_MEMORY_PREFIX}add_decision",
    f"{_CC_MEMORY_PREFIX}add_topic",
    f"{_CC_MEMORY_PREFIX}add_log",
]

_ADD_DECISION_TOOL = f"{_CC_MEMORY_PREFIX}add_decision"

_ACTIVITY_CHECKIN_TOOLS_FULL = [
    f"{_CC_MEMORY_PREFIX}check_in",
    f"{_CC_MEMORY_PREFIX}add_activity",
]

_CHECKIN_TOOL = f"{_CC_MEMORY_PREFIX}check_in"
_ADD_ACTIVITY_TOOL = f"{_CC_MEMORY_PREFIX}add_activity"

_CONTEXT_RETRIEVAL_TOOLS_FULL = [
    f"{_CC_MEMORY_PREFIX}search",
    f"{_CC_MEMORY_PREFIX}get_topics",
    f"{_CC_MEMORY_PREFIX}get_decisions",
    f"{_CC_MEMORY_PREFIX}get_logs",
    f"{_CC_MEMORY_PREFIX}get_activities",
    f"{_CC_MEMORY_PREFIX}get_by_ids",
]


def _has_tool_calls(entries: list[dict], tool_names: list[str]) -> bool:
    """entriesに指定ツールの呼び出しがあるかチェック。"""
    for entry in entries:
        message = entry.get("message", {})
        content = message.get("content", [])

        if isinstance(content, str):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name", "") in tool_names:
                return True

    return False


def has_recent_recording(entries: list[dict]) -> bool:
    """entriesにadd_decision/add_topic/add_logのツール呼び出しがあるかチェック。"""
    return _has_tool_calls(entries, _RECORDING_TOOLS_FULL)


def has_activity_checkin_calls(entries: list[dict]) -> bool:
    """entriesにcheck_in/add_activityのツール呼び出しがあるかチェック。"""
    return _has_tool_calls(entries, _ACTIVITY_CHECKIN_TOOLS_FULL)


def extract_checkin_activity_id(entries: list[dict]) -> int | None:
    """transcriptからcheck_inのtool_use入力を逆順走査し、最後のactivity_idを返す"""
    for entry in reversed(entries):
        message = entry.get("message", {})
        content = message.get("content", [])

        if isinstance(content, str):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name") == _CHECKIN_TOOL:
                tool_input = block.get("input", {})
                aid = tool_input.get("activity_id")
                if aid is not None:
                    return int(aid)

    return None


def extract_last_activity_id(transcript_path: str) -> int | None:
    """transcriptからcheck_in/add_activityのactivity_idを取得する。

    check_in: tool_useのinput.activity_idから取得
    add_activity: tool_use_idを記録し、対応するtool_resultのactivity_idから取得
    順序通りに走査し、最後に見つかったactivity_idを返す。
    """
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return None

    last_activity_id = None
    add_activity_use_ids: set[str] = set()

    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                message = entry.get("message", {})
                content = message.get("content", [])
                if isinstance(content, str):
                    continue

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")

                    if block_type == "tool_use":
                        name = block.get("name", "")
                        if name == _CHECKIN_TOOL:
                            aid = block.get("input", {}).get("activity_id")
                            if aid is not None:
                                try:
                                    last_activity_id = int(aid)
                                except (ValueError, TypeError):
                                    pass
                        elif name == _ADD_ACTIVITY_TOOL:
                            use_id = block.get("id")
                            if use_id:
                                add_activity_use_ids.add(use_id)

                    elif block_type == "tool_result":
                        use_id = block.get("tool_use_id")
                        if use_id not in add_activity_use_ids:
                            continue
                        result_content = block.get("content", "")
                        aid = _parse_activity_id_from_result(result_content)
                        if aid is not None:
                            last_activity_id = aid

    except Exception:
        pass

    return last_activity_id


def _parse_activity_id_from_result(result_content) -> int | None:
    """tool_resultのcontentからactivity_idをパースする。"""
    if isinstance(result_content, str):
        return _try_parse_activity_id(result_content)
    if isinstance(result_content, list):
        for item in result_content:
            if isinstance(item, dict) and item.get("type") == "text":
                aid = _try_parse_activity_id(item.get("text", ""))
                if aid is not None:
                    return aid
    return None


def _try_parse_activity_id(text: str) -> int | None:
    """JSON文字列からactivity_idを抽出する"""
    try:
        data = json.loads(text)
        aid = data.get("activity_id")
        if aid is not None:
            return int(aid)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


def has_context_retrieval_calls(entries: list[dict]) -> bool:
    """entriesにget系APIの呼び出しがあるかチェック。"""
    return _has_tool_calls(entries, _CONTEXT_RETRIEVAL_TOOLS_FULL)


def has_decision_without_activity(entries: list[dict]) -> bool:
    """entriesにadd_decisionがあり、かつcheck_in/add_activityがない場合True。"""
    has_decision = _has_tool_calls(entries, [_ADD_DECISION_TOOL])
    if not has_decision:
        return False
    has_activity = _has_tool_calls(entries, _ACTIVITY_CHECKIN_TOOLS_FULL)
    return not has_activity


def get_transcript_info(transcript_path: str) -> tuple[list[dict], bool]:
    """transcript全行を1パスで読み、(assistant_entries, has_skill_command)を返す。

    has_skill_commandは直近のuserエントリに<command-name>が含まれるかを示す。
    """
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return [], False

    entries: list[dict] = []
    last_user_has_command = False

    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entry_type = entry.get("type", "")
                    if entry_type == "assistant":
                        entries.append(entry)
                    elif entry_type in ("user", "human"):
                        content = entry.get("message", {}).get("content", "")
                        if isinstance(content, list) and content and isinstance(content[0], dict) and content[0].get("type") == "tool_result":
                            continue
                        text = _extract_user_content_text(entry)
                        last_user_has_command = "<command-name>" in text
                except json.JSONDecodeError:
                    continue
    except Exception:
        return [], False

    return entries, last_user_has_command
