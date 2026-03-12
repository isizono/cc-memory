"""hook共通: transcript解析ユーティリティ"""
import json
import re
from pathlib import Path


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


def _has_text_block(entry: dict) -> bool:
    """エントリにtextブロックが含まれるかチェック。"""
    content = entry.get("message", {}).get("content", [])
    if isinstance(content, str):
        return bool(content.strip())
    return any(
        isinstance(block, dict) and block.get("type") == "text"
        for block in content
    )


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


_RECORDING_TOOLS = [
    "mcp__plugin_claude-code-memory_cc-memory__add_decision",
    "mcp__plugin_claude-code-memory_cc-memory__add_topic",
    "mcp__plugin_claude-code-memory_cc-memory__add_log",
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
    return _has_tool_calls(entries, _RECORDING_TOOLS)


_ACTIVITY_CHECKIN_TOOLS = [
    "mcp__plugin_claude-code-memory_cc-memory__check_in",
    "mcp__plugin_claude-code-memory_cc-memory__add_activity",
]


def has_activity_checkin_calls(entries: list[dict]) -> bool:
    """entriesにcheck_in/add_activityのツール呼び出しがあるかチェック。"""
    return _has_tool_calls(entries, _ACTIVITY_CHECKIN_TOOLS)


_CONTEXT_RETRIEVAL_TOOLS = [
    "mcp__plugin_claude-code-memory_cc-memory__search",
    "mcp__plugin_claude-code-memory_cc-memory__get_topics",
    "mcp__plugin_claude-code-memory_cc-memory__get_decisions",
    "mcp__plugin_claude-code-memory_cc-memory__get_logs",
    "mcp__plugin_claude-code-memory_cc-memory__get_activities",
    "mcp__plugin_claude-code-memory_cc-memory__get_by_ids",
]


def has_context_retrieval_calls(entries: list[dict]) -> bool:
    """entriesにget系APIの呼び出しがあるかチェック。"""
    return _has_tool_calls(entries, _CONTEXT_RETRIEVAL_TOOLS)


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
                        text = _extract_user_content_text(entry)
                        last_user_has_command = "<command-name>" in text
                except json.JSONDecodeError:
                    continue
    except Exception:
        return [], False

    return entries, last_user_has_command
