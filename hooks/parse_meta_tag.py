#!/usr/bin/env python3
"""
transcriptの最後のassistant応答からメタタグをパースするスクリプト。

Usage:
    python parse_meta_tag.py <transcript_path>

Output (JSON):
    {"found": true, "subject_name": "xxx", "subject_id": 2, "topic_name": "yyy", "topic_id": 55}
    or
    {"found": false}
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path


def get_last_assistant_entry(transcript_path: str) -> dict | None:
    """transcriptから最後のassistantエントリを取得する"""
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return None

    # 末尾から読んで最初のassistantエントリを見つける
    try:
        result = subprocess.run(
            ["tail", "-n", "100", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().split("\n")
    except Exception:
        with open(path) as f:
            lines = f.readlines()[-100:]

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


def extract_text_from_entry(entry: dict) -> str:
    """エントリからテキスト内容を抽出する"""
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
    """
    テキストからメタタグをパースする。

    フォーマット:
    <!-- [meta] subject: xxx (id: 2) | topic: yyy (id: 55) -->
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


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"found": False}))
        sys.exit(0)

    transcript_path = sys.argv[1]

    max_retries = 3
    for attempt in range(max_retries):
        entry = get_last_assistant_entry(transcript_path)

        if entry:
            text = extract_text_from_entry(entry)
            result = parse_meta_tag(text)

            if result:
                print(json.dumps(result))
                return

        if attempt < max_retries - 1:
            time.sleep(0.3)

    print(json.dumps({"found": False}))


if __name__ == "__main__":
    main()
