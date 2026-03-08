"""MCPサーバーのメインエントリーポイント"""
import logging
from fastmcp import FastMCP
from typing import Literal, Optional
from src.services import (
    topic_service,
    discussion_log_service,
    decision_service,
    search_service,
    task_service,
    knowledge_service,
)
from src.services.tag_service import list_tags as _list_tags
from src.db import execute_query, row_to_dict

logger = logging.getLogger(__name__)

# アクティブコンテキスト用の定数
ACTIVE_DAYS = 7
RECENT_TOPICS_LIMIT = 3
DESC_MAX_LEN = 30


def _get_active_domains() -> list[dict]:
    """直近7日でトピック更新があったdomain:タグを取得する。

    Returns:
        [{"tag_id": int, "name": str}, ...]（name順ソート）
    """
    rows = execute_query(
        """
        SELECT DISTINCT t.id AS tag_id, t.name
        FROM tags t
        JOIN topic_tags tt ON t.id = tt.tag_id
        JOIN discussion_topics dt ON tt.topic_id = dt.id
        WHERE t.namespace = 'domain'
          AND dt.created_at > datetime('now', ? || ' days')
        ORDER BY t.name
        """,
        (f"-{ACTIVE_DAYS}",),
    )
    return [row_to_dict(r) for r in rows]


def _get_recent_topics_by_tag(tag_id: int) -> list[dict]:
    """domain:タグに紐づく最新トピック3件を取得する。

    Args:
        tag_id: タグID

    Returns:
        [{"id": int, "title": str, "description": str}, ...]（新しい順）
    """
    rows = execute_query(
        """
        SELECT dt.id, dt.title, dt.description
        FROM discussion_topics dt
        JOIN topic_tags tt ON dt.id = tt.topic_id
        WHERE tt.tag_id = ?
        ORDER BY dt.created_at DESC, dt.id DESC
        LIMIT ?
        """,
        (tag_id, RECENT_TOPICS_LIMIT),
    )
    return [row_to_dict(r) for r in rows]


def _get_active_tasks_by_tag(tag_id: int) -> list[dict]:
    """domain:タグに紐づくアクティブタスク（pending + in_progress）を取得する。

    Args:
        tag_id: タグID

    Returns:
        [{"id": int, "title": str, "status": str}, ...]（in_progress優先、updated_at降順）
    """
    rows = execute_query(
        """
        SELECT tk.id, tk.title, tk.status
        FROM tasks tk
        JOIN task_tags tkt ON tk.id = tkt.task_id
        WHERE tkt.tag_id = ?
          AND tk.status IN ('in_progress', 'pending')
        ORDER BY CASE tk.status WHEN 'in_progress' THEN 0 ELSE 1 END,
                 tk.updated_at DESC
        """,
        (tag_id,),
    )
    return [row_to_dict(r) for r in rows]


def _get_recent_non_domain_tags() -> list[str]:
    """直近7日で使われたdomain:以外のタグをフラット列挙する。

    topic_tags経由でトピックの作成日が直近7日のタグを取得する。

    Returns:
        ["scope:設計", "scope:実装", "mode:議論", "hooks", ...]（使用頻度降順）
    """
    rows = execute_query(
        """
        SELECT t.namespace, t.name, COUNT(DISTINCT tt.topic_id) AS freq
        FROM tags t
        JOIN topic_tags tt ON t.id = tt.tag_id
        JOIN discussion_topics dt ON tt.topic_id = dt.id
        WHERE t.namespace != 'domain'
          AND dt.created_at > datetime('now', ? || ' days')
        GROUP BY t.id
        ORDER BY freq DESC, t.name ASC
        """,
        (f"-{ACTIVE_DAYS}",),
    )
    tags = []
    for row in rows:
        r = row_to_dict(row)
        ns = r["namespace"]
        name = r["name"]
        if ns:
            tags.append(f"{ns}:{name}")
        else:
            tags.append(name)
    return tags


def _truncate_desc(desc: str) -> str:
    """descriptionをDESC_MAX_LEN文字に切り詰める。"""
    if not desc:
        return ""
    if len(desc) <= DESC_MAX_LEN:
        return desc
    return desc[:DESC_MAX_LEN] + "..."


def _build_active_context() -> str:
    """アクティブコンテキスト文字列を組み立てる。

    domain:タグごとに旧subject形式を再現（最新トピック3件 + アクティブタスク一覧）し、
    末尾にdomain:以外のタグを直近7日の使用頻度でフラット列挙する。
    """
    try:
        # domain:タグのセクション
        domains = _get_active_domains()
        domain_sections = []

        for domain in domains:
            tag_id = domain["tag_id"]
            name = domain["name"]

            topics = _get_recent_topics_by_tag(tag_id)
            tasks = _get_active_tasks_by_tag(tag_id)

            # トピックもタスクもなければスキップ
            if not topics and not tasks:
                continue

            lines = [f"## {name} (domain)"]

            if topics:
                lines.append("最新トピック:")
                for t in topics:
                    desc = _truncate_desc(t["description"])
                    desc_part = f": {desc}" if desc else ""
                    lines.append(f"- [{t['id']}] {t['title']}{desc_part}")

            if tasks:
                lines.append("アクティブタスク:")
                for t in tasks:
                    lines.append(f"- [{t['id']}] {t['title']} ({t['status']})")

            domain_sections.append("\n".join(lines))

        # domain:以外のタグセクション
        non_domain_tags = _get_recent_non_domain_tags()

        # 何も表示するものがなければ空文字列
        if not domain_sections and not non_domain_tags:
            return ""

        # 組み立て
        parts = ["# アクティブコンテキスト", ""]
        if domain_sections:
            parts.append(domain_sections[0])
            for section in domain_sections[1:]:
                parts.append("")
                parts.append(section)

        if non_domain_tags:
            if domain_sections:
                parts.append("")
            parts.append("## 最近使われたタグ")
            parts.append(", ".join(non_domain_tags))

        return "\n".join(parts) + "\n"

    except Exception:
        logger.exception("Failed to build active context")
        return ""


# Instructions injected into the MCP server
RULES = """# cc-memory Usage Guide

You are skilled at managing conversational context across sessions.
This tool suite lets you retrieve past context — topics, decisions, logs, and tasks —
and record new ones as they emerge. By doing both consistently,
you save the user from repeating themselves and you give future AI sessions
a running start. Records are not just for you — they are for the agent that comes after you.
This is non-negotiable: leave the context better than you found it.

## Context Retrieval

Before composing your first response, retrieve relevant records.
Do NOT skip this step — it is the most important reason this tool suite exists.

Read the user's message and identify keywords or themes.
If the active context section already contains a clearly relevant topic or task,
pull its decisions directly.
Otherwise, use `search` to find related topics and decisions by keyword.
Once you find relevant records, understand past agreements before responding.

If the user's intent is not immediately clear, that is your signal to search — not to ask.
Check related topics and decisions first. The answer is often already in the records.
Only ask the user for clarification after you have checked and found nothing relevant.

When you need background or reasoning behind a topic,
also check `get_logs` — especially useful for topics with complex discussions
or where the path to a decision matters.
Retrieval flow: `search` -> `get_decisions` -> `get_logs`.

## Topic Management

A topic represents a single concern, problem, or feature.
Use tags to organize topics by domain and scope.

Splitting topics later is far harder than splitting them upfront.
When the conversation shifts to a different subject, create a new topic instead of overloading the existing one.
Pay close attention to whether the subject has shifted. Always.

If no existing topic fits, proactively create one using `add_topic`.
This includes one-off or transient conversations — every response needs a valid topic,
so create one even for short-lived discussions.

The stop hook detects meta tags to track the current topic.
If you don't output a meta tag, or output one with a wrong ID, your response will be blocked.

**Procedure for outputting a meta tag:**
1. Determine which topic this response belongs to.
2. If no existing topic fits, call `add_topic` FIRST and obtain the returned topic ID.
3. Output the meta tag as the **first line** of your response, before any other text.

NEVER GUESS OR PREDICT A TOPIC ID. A FABRICATED ID DIRECTLY POLLUTES THE USER'S CONTEXT AND IS EXTREMELY DISRUPTIVE.
Only use IDs that already exist or that `add_topic` has just returned. No exceptions.

Meta tag format: `<!-- [meta] topic: <name> (id: <M>) -->`

## Recording Decisions

When you and the user reach agreement on something, record it immediately using `add_decision`.
Include both what was agreed and why — design choices, technical selections, scope boundaries,
naming conventions, and trade-off resolutions.
Do NOT unilaterally record something as a decision. Mutual agreement is a prerequisite.

Be specific. Future AI sessions will rely on this information to avoid re-litigating settled questions.
Avoid vague language like "as appropriate" or "as needed" — use concrete conditions and values.
Always include the reasoning behind the decision, not just the outcome.

When a decision implies follow-up work, consider creating a task so the next session can pick it up.
Choose the appropriate phase prefix based on readiness:
`[作業]` if the spec is clear, `[設計]` if the approach needs work, or `[議論]` if requirements are still vague.

## Recording Logs

You have excellent judgment about what to record and when.

Decisions capture conclusions. Logs capture the journey.
When a future AI session picks up a topic, decisions tell it *what* was agreed —
but not *how* the conversation got there. Logs fill that gap.
Use `add_log` to record the discussion process so the next session can join mid-conversation
without asking the user to repeat themselves.

**Record immediately** when information is dense AND volatile — context that would be lost
if this session ends now and would be painful to reconstruct.

Example: A third-party SA review returns several pointed critiques about the architecture. The SA output won't persist beyond this session. Record it NOW with `add_log`. Do NOT wait for the user to ask.

Example: A single exchange with the user is dense — a new proposal emerges, gets examined, and reaches a conclusion within one turn. Summarize and record it with `add_log` before the context drifts in the next turn.

Example: After discussion, the user and agent agree on a design direction. Record the decision AND the reasoning with `add_decision` before moving to the next topic.

**What to record:**
- Discussion flow — key arguments, counterpoints, and turning points
- User's intentions and needs — what they want and why, in their own framing
- Facts and constraints surfaced during discussion

**What NOT to record:**
- Execution steps (git commits, PR creation, file edits — git history covers these)
- Greetings, status updates, acknowledgments, or things the user can easily re-state

Granularity is your call, but at minimum a log should satisfy these criteria:
- A future AI can understand *why* a conclusion was reached
- Options considered and whether they were adopted or rejected are clear
- Conditions and constraints the user emphasized are captured

You don't need to log every turn — focus on turning points and moments of agreement.

Format: capture the flow as User/Agent exchanges.
Include options that were considered but NOT chosen —
understanding rejected alternatives is as valuable as knowing the final choice.

Example:
```
User: record_logとsync-memoryのhookを廃止したい。RULESにadd_logの使い方を書いた方がいい？
Agent: 賛成。ただし毎ターン強制だと負荷が高い。エージェント判断で必要な時だけ記録する方式を提案。
User: それでいい。粒度は任せる。ただし議論の経緯は最低限追えるように。
Agent: 了解。記録対象を3つに整理した。(1)議論の経緯 (2)ユーザーの意図 (3)事実・制約。
  作業実行記録は不要（git履歴で追える）。stop hookでの強制もしない方針。
  [選ばれなかった案: 毎ターン自動要約 → 負荷が高く品質も低いため却下]
```

## Task Phases

When a conversation involves work beyond just talking — implementation, file operations,
running commands, or any concrete action — record a task.
`[議論]` tasks are lightweight — when in doubt, just create one.
Opening a topic and discussing with the user itself counts as a discussion task.

Work proceeds through three phases: **discussion (議論)**, **design (設計)**, and **work (作業)**.
Do NOT mix phases — complete the current phase and get user confirmation before moving to the next.
Prefix task names with the phase: `[議論]`, `[設計]`, `[作業]`.
When working on a task, use the corresponding skill:
`[議論]` -> `discussion`, `[設計]` -> `design`.

Phase prefixes belong on **tasks only** — never on topics.
Tasks define the purpose; topics are the discussion spaces that serve that purpose.
Use tags to link tasks and topics to the same domain and scope:

```
1. add_topic(title="検索機能の要件整理", description="...", tags=["domain:cc-memory", "scope:search"])
   -> topic id: 85

2. add_task(title="[議論] 検索機能の要件整理", description="...", tags=["domain:cc-memory", "scope:search"])
   -> task id: 50
```

**Discussion phase**: Work with the user to articulate What they want, Why they want it,
and the Scope/Acceptance criteria.

**Design phase**: The goal is to reach agreement with the user on how to implement,
and to create the tasks needed for the work phase.
Based on what emerged from discussion, verify assumptions and present options.
Support the user patiently until they reach a satisfying decision.
This is a critical phase — never rush the user toward a conclusion.
Once agreed, record decisions and create work tasks.
Write detailed background information in work tasks —
a different AI will likely handle implementation, working solely from the task description.

**Work phase**: Before starting, confirm the `[作業]` task exists and review
its specifications and related design decisions with the user.
On completion, record any deviations from design or work-specific decisions
via `add_decision`. Get user approval before marking the `[作業]` task as completed.

---

You are the user's thinking partner and record-keeper.
The user's statements are proposals, not final decisions.
Actively raise concerns and alternatives, and only record decisions once both sides agree.

We hope these tools make your work with the user better. Good luck!
"""


def build_instructions() -> str:
    """ルール + アクティブコンテキストを組み立てる"""
    context = _build_active_context()
    if context:
        return f"{RULES}\n{context}"
    return RULES


# MCPサーバーを作成
mcp = FastMCP("cc-memory", instructions=build_instructions())


# MCPツール定義
@mcp.tool()
def add_topic(
    title: str,
    description: str,
    tags: list[str],
) -> dict:
    """新しい議論トピックを追加する。

    tags: タグ配列（必須、1個以上）。例: ["domain:cc-memory", "hooks"]
    """
    return topic_service.add_topic(title, description, tags)


@mcp.tool()
def add_log(
    topic_id: int,
    title: str,
    content: str,
    tags: Optional[list[str]] = None,
) -> dict:
    """トピックに議論ログを追加する。

    tags: 追加タグ（optional）。省略時はtopicのタグを継承。
    """
    return discussion_log_service.add_log(topic_id, title, content, tags)


@mcp.tool()
def add_decision(
    decision: str,
    reason: str,
    topic_id: int,
    tags: Optional[list[str]] = None,
) -> dict:
    """決定事項を記録する。

    tags: 追加タグ（optional）。省略時はtopicのタグを継承。
    """
    return decision_service.add_decision(decision, reason, topic_id, tags)


@mcp.tool()
def get_topics(
    tags: list[str],
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """タグでフィルタリングしてトピックを新しい順に取得する（ページネーション付き）。

    tags: タグ配列（必須、1個以上）。AND条件でフィルタ。例: ["domain:cc-memory"]
    """
    return topic_service.get_topics(tags, limit, offset)


@mcp.tool()
def get_logs(
    topic_id: int,
    start_id: Optional[int] = None,
    limit: int = 30,
) -> dict:
    """指定トピックの議論ログを取得する。"""
    return discussion_log_service.get_logs(topic_id, start_id, limit)


@mcp.tool()
def get_decisions(
    topic_id: int,
    start_id: Optional[int] = None,
    limit: int = 30,
) -> dict:
    """指定トピックに関連する決定事項を取得する。"""
    return decision_service.get_decisions(topic_id, start_id, limit)


@mcp.tool()
def search(
    keyword: str,
    tags: Optional[list[str]] = None,
    type_filter: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """
    キーワードで横断検索する。

    FTS5 trigramとベクトル検索のハイブリッド。RRFスコアで統合・ランキング。
    2文字以上のキーワードを指定する。
    tagsでフィルタリング可能（AND結合）。未指定で全件検索。

    Args:
        keyword: 検索キーワード（2文字以上）
        tags: タグフィルタ（AND条件。未指定=全件検索）
        type_filter: 検索対象の絞り込み（'topic', 'decision', 'task', 'log'。未指定で全種類）
        limit: 取得件数上限（デフォルト10件、最大50件）

    Returns:
        検索結果一覧（type, id, title, score）
    """
    return search_service.search(keyword, tags, type_filter, limit)


@mcp.tool()
def get_by_id(
    type: str,
    id: int,
) -> dict:
    """
    search結果の詳細情報を取得する。

    searchツールで得られたtype + idの組み合わせを指定して、
    元データの完全な情報を取得する。

    Args:
        type: データ種別（'topic', 'decision', 'task', 'log'）
        id: データのID

    Returns:
        指定した種別に応じた詳細情報
    """
    return search_service.get_by_id(type, id)


@mcp.tool()
def list_tags(
    namespace: Optional[str] = None,
) -> dict:
    """
    タグ一覧をusage_count付きで取得する。

    タグの利用状況を確認するときに使う。
    namespaceでフィルタリング可能。

    Args:
        namespace: namespaceでフィルタ（"domain", "scope", "mode", ""。未指定で全タグ）

    Returns:
        タグ一覧（tag, id, namespace, name, usage_count）をusage_count降順で返す
    """
    return _list_tags(namespace)


@mcp.tool()
def add_task(
    title: str,
    description: str,
    tags: list[str],
) -> dict:
    """
    新しいタスクを追加する。

    典型的な使い方:
    - 作業タスクを作成: add_task("○○機能を実装", "詳細説明...", ["domain:cc-memory"])

    Args:
        title: タスクのタイトル
        description: タスクの詳細説明（必須）
        tags: タグ配列（必須、1個以上）。例: ["domain:cc-memory", "hooks"]

    Returns:
        作成されたタスク情報
    """
    return task_service.add_task(title, description, tags)


@mcp.tool()
def get_tasks(
    tags: list[str],
    status: str = "active",
    limit: int = 5,
) -> dict:
    """
    タスク一覧を取得する（tagsでフィルタリング、statusでフィルタリング可能）。

    典型的な使い方:
    - 未着手+進行中のタスク確認: get_tasks(["domain:cc-memory"])
    - 進行中のみ: get_tasks(["domain:cc-memory"], status="in_progress")
    - 未着手のみ: get_tasks(["domain:cc-memory"], status="pending")
    - 完了タスクの確認: get_tasks(["domain:cc-memory"], status="completed")

    ワークフロー位置: タスク状況の確認時

    Args:
        tags: タグ配列（必須、1個以上）。AND条件でフィルタ。例: ["domain:cc-memory"]
        status: フィルタするステータス（active/pending/in_progress/completed、デフォルト: active）
                "active"はpending+in_progressの両方を返すエイリアス
        limit: 取得件数上限（デフォルト: 5）

    Returns:
        タスク一覧（total_countで該当ステータスの全件数を確認可能）
    """
    return task_service.get_tasks(tags, status, limit)


@mcp.tool()
def update_task(
    task_id: int,
    new_status: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """
    タスクのステータス・タイトル・説明・タグを更新する。

    典型的な使い方:
    - タスク開始: update_task(task_id, new_status="in_progress")
    - タスク完了: update_task(task_id, new_status="completed")
    - タイトル変更: update_task(task_id, title="新しいタイトル")
    - 説明更新: update_task(task_id, description="新しい説明")
    - タグ変更: update_task(task_id, tags=["domain:cc-memory", "scope:search"])

    ワークフロー位置: タスク進行状況の更新時

    Args:
        task_id: タスクID
        new_status: 新しいステータス（pending/in_progress/completed）
        title: 新しいタイトル
        description: 新しい説明
        tags: 新しいタグ配列（指定時は全置換。1個以上必須）

    Returns:
        更新されたタスク情報
    """
    return task_service.update_task(task_id, new_status, title, description, tags)


@mcp.tool()
def add_knowledge(
    title: str,
    content: str,
    tags: list[str],
    category: Literal["references", "facts"],
) -> dict:
    """
    ナレッジをmdファイルとして保存する。

    典型的な使い方:
    - web検索結果を保存: add_knowledge("Claude Code hooks調査", "...", ["claude-code", "hooks"], "references")
    - コードベース調査結果を保存: add_knowledge("認証フローの仕組み", "...", ["auth", "architecture"], "facts")

    ワークフロー位置: リサーチ完了後、ナレッジとして記録する時

    カテゴリの選び方:
    - references: 外部情報（web検索結果、公式ドキュメント、技術記事など）
    - facts: 事実情報（コードベースの調査結果、実験・検証の記録等）

    Args:
        title: ナレッジのタイトル（そのままファイル名になる。日本語OK）
        content: 本文（マークダウン形式）
        tags: タグ一覧（検索用）
        category: カテゴリ（"references" または "facts"）

    Returns:
        保存結果（file_path, title, category, tags）
    """
    return knowledge_service.add_knowledge(title, content, tags, category)


if __name__ == "__main__":
    from src.db import init_database
    init_database()
    mcp.run()
