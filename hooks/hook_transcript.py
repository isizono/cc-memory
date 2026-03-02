"""hook共通: transcript解析ユーティリティ"""
import json
import re
from pathlib import Path


def get_last_assistant_entry(transcript_path: str) -> dict | None:
    """transcriptから最後のassistantエントリを取得する。
    末尾100行を読み、逆順で最初のassistantエントリを返す。"""
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return None

    # 末尾から読んで最初のassistantエントリを見つける
    try:
        with open(path) as f:
            lines = f.readlines()[-100:]
    except Exception:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") == "assistant":
                return entry
        except json.JSONDecodeError:
            continue

    return None


def get_assistant_entries(transcript_path: str, last_n: int | None = None) -> list[dict]:
    """transcriptからassistantエントリを取得する。
    last_nが指定されていれば直近N件のみ返す。"""
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return []

    entries = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "assistant":
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

    if last_n is not None and entries:
        return entries[-last_n:]
    return entries


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
    <!-- [meta] subject: xxx (id: N) | topic: yyy (id: M) -->

    Returns:
        {"found": True, "subject_name": ..., "subject_id": ..., "topic_name": ..., "topic_id": ...}
        or None
    """
    # HTMLコメント形式のメタタグを探す
    pattern = r'<!--\s*\[meta\]\s*subject:\s*(.+?)\s*\(id:\s*(\d+)\)\s*\|\s*topic:\s*(.+?)\s*\(id:\s*(\d+)\)\s*-->'
    match = re.search(pattern, text)

    if match:
        return {
            "found": True,
            "subject_name": match.group(1).strip(),
            "subject_id": int(match.group(2)),
            "topic_name": match.group(3).strip(),
            "topic_id": int(match.group(4)),
        }

    return None


def find_tool_calls_for_topic(entries: list[dict], topic_id: int) -> bool:
    """entriesからadd_decision/add_logのツール呼び出しを探し、
    指定topic_idに対する呼び出しがあるかチェックする。"""
    target_tools = [
        "mcp__plugin_claude-code-memory_cc-memory__add_decision",
        "mcp__plugin_claude-code-memory_cc-memory__add_log",
    ]

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

            tool_name = block.get("name", "")
            if tool_name not in target_tools:
                continue

            # ツール呼び出しの引数をチェック
            tool_input = block.get("input", {})
            called_topic_id = tool_input.get("topic_id")

            if called_topic_id is not None:
                try:
                    if int(called_topic_id) == topic_id:
                        return True
                except (ValueError, TypeError):
                    continue

    return False


_RECORDING_TOOLS = [
    "mcp__plugin_claude-code-memory_cc-memory__add_decision",
    "mcp__plugin_claude-code-memory_cc-memory__add_topic",
]


def has_recent_recording(entries: list[dict]) -> bool:
    """entriesにadd_decision/add_topicのツール呼び出しがあるかチェック。"""
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
            if block.get("name", "") in _RECORDING_TOOLS:
                return True

    return False
