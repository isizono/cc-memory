"""MCPサーバーのメインエントリーポイント"""
import logging
from fastmcp import FastMCP
from typing import Literal, Optional
from src.db import execute_query
from src.services import (
    subject_service,
    topic_service,
    discussion_log_service,
    decision_service,
    search_service,
    task_service,
    knowledge_service,
)

logger = logging.getLogger(__name__)

ACTIVE_SUBJECT_DAYS = 7
RECENT_TOPICS_LIMIT = 3
DESC_MAX_LEN = 30


def _get_active_subjects() -> list[dict]:
    """直近7日以内にトピック更新があったサブジェクトを取得する"""
    rows = execute_query(
        """
        SELECT DISTINCT s.id, s.name
        FROM subjects s
        JOIN discussion_topics t ON s.id = t.subject_id
        WHERE t.created_at > datetime('now', ? || ' days')
        ORDER BY s.id
        """,
        (f"-{ACTIVE_SUBJECT_DAYS}",),
    )
    return [{"id": row["id"], "name": row["name"]} for row in rows]


def _get_recent_topics(subject_id: int) -> list[dict]:
    """サブジェクトの最新トピック3件を取得する"""
    rows = execute_query(
        """
        SELECT id, title, description
        FROM discussion_topics
        WHERE subject_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (subject_id, RECENT_TOPICS_LIMIT),
    )
    results = []
    for row in rows:
        desc = row["description"] or ""
        if len(desc) > DESC_MAX_LEN:
            desc = desc[:DESC_MAX_LEN] + "..."
        results.append({"id": row["id"], "title": row["title"], "description": desc})
    return results


def _get_active_tasks(subject_id: int) -> list[dict]:
    """サブジェクトのin_progress・pendingタスクを取得する"""
    rows = execute_query(
        """
        SELECT id, title, status
        FROM tasks
        WHERE subject_id = ? AND status IN ('in_progress', 'pending')
        ORDER BY
            CASE status WHEN 'in_progress' THEN 0 ELSE 1 END,
            updated_at DESC
        LIMIT 20
        """,
        (subject_id,),
    )
    return [{"id": row["id"], "title": row["title"], "status": row["status"]} for row in rows]


def _build_active_context() -> str:
    """アクティブサブジェクトのコンテキスト文字列を組み立てる"""
    try:
        subjects = _get_active_subjects()
        if not subjects:
            return ""

        lines = ["# アクティブサブジェクト（直近7日）\n"]
        for s in subjects:
            lines.append(f"## {s['name']} (id: {s['id']})")

            topics = _get_recent_topics(s["id"])
            if topics:
                lines.append("最新トピック:")
                for t in topics:
                    lines.append(f"- [{t['id']}] {t['title']}: {t['description']}")

            tasks = _get_active_tasks(s["id"])
            if tasks:
                lines.append("アクティブタスク:")
                for task in tasks:
                    lines.append(f"- [{task['id']}] {task['title']} ({task['status']})")

            lines.append("")

        return "\n".join(lines)
    except Exception:
        logger.warning("Failed to build active context", exc_info=True)
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
Topics support parent-child relationships, so create child topics when a discussion branches off.

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

Meta tag format: `<!-- [meta] subject: <name> (id: <N>) | topic: <name> (id: <M>) -->`

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
User: record_logとsync_memoryのhookを廃止したい。RULESにadd_logの使い方を書いた方がいい？
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
Link tasks and topics using the `topic_id` parameter:

```
1. add_topic(subject_id=2, title="検索機能の要件整理", description="...")
   -> topic id: 85

2. add_task(subject_id=2, title="[議論] 検索機能の要件整理", description="...", topic_id=85)
   -> task id: 50

3. As discussion branches off, create child topics under topic 85.
   The task (id:50) remains the single source of purpose.
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
def add_subject(
    name: str,
    description: str,
) -> dict:
    """
    新しいサブジェクトを追加する。

    Subjectとは「独立した関心事・取り組み」の単位。リポジトリではなく「何について話すか」で区切る。

    新規作成すべきとき:
    - 既存Subjectのどれとも関係ない話題が始まった
    - 別プロダクト・サービスの話になった
    - 新しい取り組みに着手する

    既存Subjectを使うとき:
    - 既存Subjectの関心事の「中」の話（→ add_topicで新規Topic）

    判断に迷ったらユーザーに「どのSubjectで進める？」と確認すること。
    """
    return subject_service.add_subject(name, description)


@mcp.tool()
def list_subjects() -> dict:
    """サブジェクト一覧を取得する。id + name のみ返す軽量版。"""
    return subject_service.list_subjects()


@mcp.tool()
def add_topic(
    subject_id: int,
    title: str,
    description: str,
    parent_topic_id: Optional[int] = None,
) -> dict:
    """新しい議論トピックを追加する。"""
    return topic_service.add_topic(subject_id, title, description, parent_topic_id)


@mcp.tool()
def add_log(topic_id: int, content: str) -> dict:
    """トピックに議論ログを追加する。"""
    return discussion_log_service.add_log(topic_id, content)


@mcp.tool()
def add_decision(
    decision: str,
    reason: str,
    topic_id: int,
) -> dict:
    """決定事項を記録する。"""
    return decision_service.add_decision(decision, reason, topic_id)


@mcp.tool()
def get_topics(
    subject_id: int,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """サブジェクト内のトピックを新しい順に取得する（ページネーション付き）。
    各トピックにancestorsフィールド（直親→祖先の順、最大5段、{id, title}のみ）を付与。"""
    return topic_service.get_topics(subject_id, limit, offset)


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
    subject_id: int,
    keyword: str,
    type_filter: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """
    サブジェクト内をキーワードで横断検索する。

    FTS5 trigramトークナイザによる部分文字列マッチ。3文字以上のキーワードを指定する。
    結果はBM25スコア順でランキングされる。
    詳細情報が必要な場合は get_by_id(type, id) で取得する。

    Args:
        subject_id: サブジェクトID
        keyword: 検索キーワード（3文字以上）
        type_filter: 検索対象の絞り込み（'topic', 'decision', 'task'。未指定で全種類）
        limit: 取得件数上限（デフォルト10件、最大50件）

    Returns:
        検索結果一覧（type, id, title, score）
    """
    return search_service.search(subject_id, keyword, type_filter, limit)


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
        type: データ種別（'topic', 'decision', 'task'）
        id: データのID

    Returns:
        指定した種別に応じた詳細情報
    """
    return search_service.get_by_id(type, id)


@mcp.tool()
def add_task(
    subject_id: int,
    title: str,
    description: str,
    topic_id: Optional[int] = None,
) -> dict:
    """
    新しいタスクを追加する。

    典型的な使い方:
    - 作業タスクを作成: add_task(subject_id, "○○機能を実装", "詳細説明...")

    ワークフロー位置: 作業タスクの整理・管理開始時

    Args:
        subject_id: サブジェクトID
        title: タスクのタイトル
        description: タスクの詳細説明（必須）
        topic_id: 関連トピックID（optional）

    Returns:
        作成されたタスク情報
    """
    return task_service.add_task(subject_id, title, description, topic_id)


@mcp.tool()
def get_tasks(
    subject_id: int,
    status: str = "in_progress",
    limit: int = 5,
) -> dict:
    """
    タスク一覧を取得する（statusでフィルタリング可能）。

    典型的な使い方:
    - 進行中のタスク確認: get_tasks(subject_id)
    - 未着手のタスク確認: get_tasks(subject_id, status="pending")
    - 完了タスクの確認: get_tasks(subject_id, status="completed")

    ワークフロー位置: タスク状況の確認時

    Args:
        subject_id: サブジェクトID
        status: フィルタするステータス（pending/in_progress/completed、デフォルト: in_progress）
        limit: 取得件数上限（デフォルト: 5）

    Returns:
        タスク一覧（total_countで該当ステータスの全件数を確認可能）
    """
    return task_service.get_tasks(subject_id, status, limit)


@mcp.tool()
def update_task(
    task_id: int,
    new_status: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    topic_id: Optional[int] = None,
) -> dict:
    """
    タスクのステータス・タイトル・説明を更新する。

    典型的な使い方:
    - タスク開始: update_task(task_id, new_status="in_progress")
    - タスク完了: update_task(task_id, new_status="completed")
    - タイトル変更: update_task(task_id, title="新しいタイトル")
    - 説明更新: update_task(task_id, description="新しい説明")
    - トピック紐付け: update_task(task_id, topic_id=85)

    ワークフロー位置: タスク進行状況の更新時

    Args:
        task_id: タスクID
        new_status: 新しいステータス（pending/in_progress/completed）
        title: 新しいタイトル
        description: 新しい説明
        topic_id: 関連トピックID

    Returns:
        更新されたタスク情報
    """
    return task_service.update_task(task_id, new_status, title, description, topic_id)


@mcp.tool()
def update_subject(
    subject_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """
    サブジェクトの名前・説明を更新する。

    典型的な使い方:
    - リネーム: update_subject(subject_id, name="新しい名前")
    - 説明更新: update_subject(subject_id, description="新しい説明")

    Args:
        subject_id: サブジェクトID
        name: 新しいサブジェクト名
        description: 新しい説明

    Returns:
        更新されたサブジェクト情報
    """
    return subject_service.update_subject(subject_id, name, description)


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
