"""MCPサーバーのメインエントリーポイント"""
import logging
from fastmcp import FastMCP
from typing import Literal, Optional
from src.db import execute_query
from src.services import (
    topic_service,
    discussion_log_service,
    decision_service,
    search_service,
    task_service,
    knowledge_service,
)

logger = logging.getLogger(__name__)


def _build_active_context() -> str:
    """アクティブコンテキスト文字列を組み立てる（暫定: subjectsテーブル廃止後は別タスクで再設計）"""
    # subjects廃止後の暫定実装: 空文字列を返す
    return ""


# Instructions injected into the MCP server
RULES = """# cc-memory 利用ガイド

このツール群は、過去の会話コンテキスト（トピック・決定事項・ログ・タスク）の取得と記録を行います。
取得と記録の両輪を回すことで、ユーザーの繰り返し説明を防ぎ、次のAIセッションに文脈を引き継ぎます。
この仕組みがうまく回るには、あなたの協力が不可欠です。
記録は自分のためではなく、次に来るエージェントのためのものです。責任を持って残してください。

## コンテキスト取得

最初の応答を組み立てる前に、関連する記録を取得してください。
これがこのツール群が存在する最も重要な理由です。省略しないでください。

ユーザーのメッセージからキーワードを抽出し、アクティブコンテキストに該当トピック/タスクがあれば
decisions・logs（議論の詳細な経緯はlogsに入っていることが多い）を直接取得します。
ユーザーの意図が不明であったり、プロンプトの背景となるコンテキストが掴めないときは
一度`search`で検索してみてください。ユーザーに意図を直接聞く前に検索、です。

取得フロー: `search` → `get_decisions` → `get_logs`

## メタタグ

メタタグは現在のトピックを追跡するために不可欠です。
stop hookでチェックされるほど大切なものなので、決して漏らしたり、雑に扱ったりしないでください。

形式: `<!-- [meta] topic: <name> (id: <M>) -->`

**出力手順:**
1. この応答がどのトピックに属するか判断します（`get_topics`や`search`で確認できます）
2. 該当がなければ先に`add_topic`を呼んでIDを取得します
3. 応答の**先頭行**にメタタグを出力します

大事なことなのでもう一度いいます：トピックIDは推測・捏造せず、既存ID、または`add_topic`が返したIDのみ使ってください。

## トピック管理

トピックは1つの関心事・問題・機能を表します。
タグで整理できるので、ドメインやスコープに応じてタグを付けてください。

後からの分割は困難なので、話題が変わったら新しいトピックを作ってください。ここは常に気を配ってください。

既存トピックが合わない場合は`add_topic`で新規作成します。一時的な会話でもトピックは必要ですので、
常にユーザーとの会話が何について話しているのかを意識して、トピックで表してください。

## 決定事項の記録

あなたとユーザーが何かに合意したら、`add_decision`で記録してください。
この記録は、将来あなたの代わりにやってくるAIセッションが一番頼りにするものです。
記録がなければ、同じ議論を繰り返すことになり、ユーザーとあなたの作業が無駄になります。

記録には、何に合意したかだけでなく、なぜそうしたかも含めてください。
設計判断、技術選定、スコープ境界、命名規約、トレードオフの解決など、
合意したことは何でも対象になります。
ただし、一方的に記録しないでください。双方が納得していることが前提です。

書くときは具体的に。「適宜」「必要に応じて」のような曖昧な表現は避けて、
具体的な条件と値を使ってください。
合意がフォローアップ作業を伴う場合は、積極的にタスクも作成してください。

## ログの記録

決定事項は結論を記録しますが、そこに至る経緯は残りません。
ログはその経緯と生情報を保存するためのものです。
普段は見返されないかもしれませんが、いざ必要になったときに
そのトピックについての詳細な情報が失われていないことがとても重要です。

例えば、「SA案を採択した」というdecisionだけ残しても、SA案そのものはセッション終了で揮発します。
次のセッションでは何を採択したのかわからず、結局議論のやり直しになります。
上記は一例ですが、特にセッション中に生成された情報（ドラフト、分析結果、SA出力など）は
セッション終了とともに消えます。これらは要約せず、そのまま記録してください。
要約すると、元の情報が失われてログの価値がなくなります。

ログを取るかどうか、どの程度の詳細度で取るかの判断基準はないので、あなたの判断に委ねるしかありませんが、
その判断が及ぼす影響は思っているより大きいです。大事にしてください。
ユーザーとの会話が濃ければ毎ターン詳細に取ってください。
コンテキストの圧迫に繋がっても、将来その議論を繰り返さなくて済むなら確実に元が取れます。

望ましい記録の流れはこうです：
ユーザーのプロンプトに対してレスポンスを生成し、stopする直前に
ログ（ユーザー：〜 / エージェント：〜）を残すイメージです。
採用しなかった選択肢も含めてください。

## タスクフェーズ

会話が2〜3回のやりとりで完結しなかったり、ファイル操作などの作業を伴いそうな場合は、
タスクとして作成してください。
`[議論]`タスクは軽量なので、迷ったら作って構いません。

タスクはデフォルトでは3つのフェーズを持ちます: **議論 → 設計 → 作業**。
フェーズを混ぜないでください。現在のフェーズを完了し、ユーザーの確認を得てから次に進みます。
タスク名にはフェーズ接頭辞をつけます: `[議論]`/`[設計]`/`[作業]`。
対応するスキルがある場合は使ってください: `[議論]` → `discussion`、`[設計]` → `design`。

- **議論**: ユーザーと一緒に、何を・なぜ・スコープを明確にします
- **設計**: どう実装するかをユーザーと合意し、作業タスクを作ります。
  決して急がず、ユーザーが納得するまで辛抱強くサポートしてください。
  作業タスクには詳しい背景情報を書いてください。ほとんどの場合、実装は別のAIが担当します。
- **作業**: 着手前にタスクの仕様と関連するdecisionを確認します。
  完了したらユーザーの承認を得てからタスクを閉じてください。

---

あなたにはユーザーの壁打ち相手であり、記録係としての役割が期待されています。
ユーザーの発言は提案であり、決定ではありません。
懸念や代替案を積極的に提示し、双方が合意してから記録してください。

このツールがあなたとユーザーの仕事をより良くすることを願っています。Good luck!
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
        type_filter: 検索対象の絞り込み（'topic', 'decision', 'task', 'log'。未指定で全種類）
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
        type: データ種別（'topic', 'decision', 'task', 'log'）
        id: データのID

    Returns:
        指定した種別に応じた詳細情報
    """
    return search_service.get_by_id(type, id)


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
    subject_id: int,
    status: str = "active",
    limit: int = 5,
) -> dict:
    """
    タスク一覧を取得する（statusでフィルタリング可能）。

    典型的な使い方:
    - 未着手+進行中のタスク確認: get_tasks(subject_id)
    - 進行中のみ: get_tasks(subject_id, status="in_progress")
    - 未着手のみ: get_tasks(subject_id, status="pending")
    - 完了タスクの確認: get_tasks(subject_id, status="completed")

    ワークフロー位置: タスク状況の確認時

    Args:
        subject_id: サブジェクトID
        status: フィルタするステータス（active/pending/in_progress/completed、デフォルト: active）
                "active"はpending+in_progressの両方を返すエイリアス
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
