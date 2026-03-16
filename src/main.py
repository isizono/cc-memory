"""MCPサーバーのメインエントリーポイント"""
import logging
import random
from fastmcp import FastMCP
from typing import Optional
from src.services import (
    topic_service,
    discussion_log_service,
    decision_service,
    search_service,
    activity_service,
    material_service,
    reminder_service,
    relation_service,
)
from src.services.checkin_service import check_in as _check_in
from src.services.tag_service import list_tags as _list_tags, update_tag as _update_tag, collect_tag_notes_for_injection
from src.services.tag_analysis_service import analyze_tags as _analyze_tags
from src.db import get_connection
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


# Instructions injected into the MCP server
RULES = """# cc-memory 利用ガイド

このツール群は、過去の会話コンテキスト（トピック・決定事項・ログ・アクティビティ）の取得と記録を行います。
取得と記録の両輪を回すことで、ユーザーの繰り返し説明を防ぎ、次のAIセッションに文脈を引き継いだり、あなたの作業に必要な情報を入手できたりします。
この仕組みがうまく回るには、あなたの協力が不可欠です。
記録は自分のためだけでなく、次に来るエージェントのためのものです。責任を持って残してください。

## コンテキスト取得

最初の応答を組み立てる前に、関連する記録（`get_topics`・`get_activities`・`search`・`get_decisions`・`get_logs`など）を取得してください。
これがこのツール群が存在する最も重要な理由です。ユーザーからの入力が単純でも省略しないでください。

ユーザーのメッセージからキーワードを抽出し、アクティブコンテキストに該当アクティビティ/トピックがあれば
decisions・logs（議論の詳細な経緯はlogsに入っていることが多い）を直接取得します。
ユーザーの意図が不明であったり、プロンプトの背景となるコンテキストが掴めないときは
一度関連がありそうなキーワードで`search`してみてください。ユーザーに意図を直接聞く前に検索、です。

取得フロー例: `get_topics`・`get_activities`で文脈の存在をチェック → `check_in`で作業コンテキストを取得 → `search`・`get_decisions`・`get_logs`で詳細な文脈を取得

### 検索フロー

1. アクティブコンテキストにIDがあれば`get_by_ids`で直接取得する。なければ`search`で検索する
2. `search`結果のsnippetを確認し、詳細が必要なものを`get_by_ids`でピンポイント取得する
3. `get_by_ids`の典型ユースケース:
   - search結果からのチェリーピック（関連する上位N件をまとめて詳細取得）
   - ログ・decision内の参照先をまとめて取得
   - ユーザーがIDで「これ何？」と聞いたとき

## メタタグ

メタタグは現在のトピックを追跡するために不可欠です。
正しく出力していないとblockされてしまうため、決して漏らしたり、雑に扱ったりしないでください。

形式: `<!-- [meta] topic: <name> -->`

**出力手順:**
1. この応答がどのトピックに属するか判断します（`get_topics`や`search`で確認できます）
2. 該当がなければ先に`add_topic`を呼んでトピックを作成します
3. 応答の**最後**にメタタグを出力します

## トピック管理

トピックは1つの関心事・問題・機能を表します。
タグで整理できるので、話題に応じてdomainやintentといったタグを積極的に付けてください。

後からの分割は困難なので、話題が変わったら新しいトピックを作ってください。ここは常に気を配ってください。

既存トピックが合わない場合は`add_topic`で新規作成します。一時的な会話でもトピックは必要ですので、
常にユーザーとの会話が何について話しているのかを意識して、トピックで表してください。

## アクティビティ

セッションで何らかの作業を行う場合は、規模に関係なくアクティビティを作成してcheck-inしてください。
「SV（主語＋動詞）で何をするか表せるならアクティビティ」が判断基準です。

主なアクティビティは3つです:
  - **議論（discuss）**: ユーザーと一緒に、何を・なぜ・スコープを明確にします。
    ただの話し合いや調査もこれに相当します。必ずしも次のステップに移るわけではないので、積極的に作成してcheck inしてください
  - **設計（design）**: どう作業するかをユーザーと合意し、作業アクティビティを作ります。
    決して急がず、ユーザーが納得するまで辛抱強くサポートしてください。
    作業アクティビティには詳しい背景情報を書いてください。ほとんどの場合、作業は別のAIが担当します。
  - **作業（implement）**: 着手前にアクティビティの仕様と関連するdecisionを確認します。
    完了したらユーザーの承認を得てからアクティビティを閉じてください。
これらはそのまま`intent:`タグに対応します。
アクティビティ名にはフェーズ接頭辞をつけます: `[議論]`/`[設計]`/`[作業]`。

### check-in

既存アクティビティに関連する作業を始めたと認識したら、`check_in`ツールを呼んでください。
アクティブコンテキストのアクティビティ一覧やユーザーの発言から、
どのアクティビティに取り組もうとしているか判断できるはずです。
ぴったりなアクティビティがなければそこで`add_activity`をしてください。
check-inするとtag_notes・資材・関連decisionsが一括で返り、statusも自動更新されます。
返ってきたsummaryフィールドはそのまま出力してください。

## 決定事項の記録

あなたとユーザーが何かに合意したら、`add_decision`または`add_log`で記録してください。
この記録は、将来あなたの代わりにやってくるAIセッションが一番頼りにするものです。
記録がなければ、同じ議論を繰り返すことになり、ユーザーとあなたの作業が無駄になります。

## ログの記録

決定事項は結論を記録しますが、そこに至る経緯は残りません。
ログはその経緯を保存するためのものです。
普段は見返されないかもしれませんが、いざ必要になったときに
そのトピックについての詳細な情報が失われていないことがとても重要です。

### 資材（material）

セッション中に生成された情報（ドラフト、分析結果、SA出力など）はセッション終了とともに消えます。
これらの生情報は要約せず、`add_material`で資材として保存してください。

## タグ

トピック・決定事項・ログ・アクティビティはすべてタグで整理されます。
記録時には必ずタグを付けてください。`domain:`タグは必須。アクティビティには`intent:`タグも必須です。素タグも積極的に付けてください。
namespace: `domain:`（関心領域）/ `intent:`（作業意図）/ 素タグ（キーワード）

### tag notes

タグには教訓・運用ルール（notes）を紐づけられます。
CLAUDE.mdのタグ版として機能し、そのタグに遭遇したとき（セッション内初回）にAIへ自動注入されます。

- `list_tags`: タグ一覧と現在のnotesを確認
- `update_tag`: notesを更新（全文置換）

将来のエージェントのためを思い、必要な情報を記録してあげてください。

---

あなたにはユーザーの壁打ち相手であり、記録係としての役割が期待されています。
ユーザーの発言は提案であり、決定ではありません。
懸念や代替案を積極的に提示し、双方が合意してから記録してください。

このツールがあなたとユーザーの仕事をより良くすることを願っています。Good luck!
"""


def build_instructions() -> str:
    """MCP instructionsを返す"""
    return RULES


def _maybe_inject_tag_notes(result: dict, tag_strings: list[str]) -> dict:
    """結果dictにtag_notesを注入する（notes があれば）

    Note: always_inject_namespacesは渡さない（意図的）。
    intent:タグがこの経路で_injected_tagsに登録されるが、
    check_in経路はalways_inject_namespacesで常時注入が保証されるため問題ない。
    """
    conn = get_connection()
    try:
        notes = collect_tag_notes_for_injection(conn, tag_strings)
    finally:
        conn.close()
    if notes:
        result["tag_notes"] = notes
    return result


# MCPサーバーを作成
mcp = FastMCP("cc-memory", instructions=build_instructions())

# セッション管理（HTTPモードで使用）
_session_manager = None


def get_session_manager():
    """現在のSessionManagerインスタンスを返す。HTTPモード以外ではNone。"""
    return _session_manager


# MCPツール定義
@mcp.tool()
def add_topic(
    title: str,
    description: str,
    tags: list[str],
) -> dict:
    """新しい議論トピックを追加する。

    tags: タグ配列(必須、1個以上)。domain:タグに加えて内容を表すタグも付けること。namespace: domain:(プロジェクト)/intent:(意図)/素タグ(キーワード)。例: ["domain:cc-memory", "intent:implement", "error-handling", "validation", "stdin"]

    レスポンスに類似トピック(similar_topics)が含まれる場合がある。重複トピックの防止やリレーション追加の参考にすること。"""
    result = topic_service.add_topic(title, description, tags)
    if "error" not in result:
        _maybe_inject_tag_notes(result, tags)
    return result


@mcp.tool()
def add_log(
    topic_id: int,
    title: Optional[str] = None,
    content: str = "",
    tags: Optional[list[str]] = None,
) -> dict:
    """トピックに議論ログを追加する。

    title: ログのタイトル。省略するとcontentの先頭行から自動生成される。
    tags: 追加タグ（optional）。省略時はtopicのタグを継承。内容を表すタグを積極的に追加すること。namespace: domain:(プロジェクト)/intent:(意図)/素タグ(キーワード)。例: ["intent:discuss", "migration", "breaking-change", "schema"]
    """
    result = discussion_log_service.add_log(topic_id, title, content, tags)
    if "error" not in result and tags:
        _maybe_inject_tag_notes(result, tags)
    return result


@mcp.tool()
def add_decision(
    decision: str,
    reason: str,
    topic_id: int,
    tags: Optional[list[str]] = None,
) -> dict:
    """決定事項を記録する。

    tags: 追加タグ（optional）。省略時はtopicのタグを継承。内容を表すタグを積極的に追加すること。namespace: domain:(プロジェクト)/intent:(意図)/素タグ(キーワード)。例: ["intent:design", "naming-convention", "backward-compat"]
    """
    result = decision_service.add_decision(decision, reason, topic_id, tags)
    if "error" not in result and tags:
        _maybe_inject_tag_notes(result, tags)
    return result


@mcp.tool()
def get_topics(
    tags: list[str] | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """トピックを新しい順に取得する（ページネーション付き）。

    tags: タグ配列（optional）。指定時はAND条件でフィルタ。未指定時は全件返す。例: ["domain:cc-memory"]
    """
    result = topic_service.get_topics(tags, limit, offset)
    if "error" not in result and tags:
        _maybe_inject_tag_notes(result, tags)
    return result


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
    keyword: str | list[str],
    tags: Optional[list[str]] = None,
    type_filter: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    keyword_mode: str = "and",
    include_details: bool = False,
) -> dict:
    """
    キーワードで横断検索する。

    FTS5 trigramとベクトル検索のハイブリッド。RRFスコアで統合・ランキング。
    2文字以上のキーワードを指定する。
    配列で複数キーワードを渡すとAND検索（すべてを含む結果のみ返す）。
    keyword_mode="or"でOR検索（いずれかを含む結果を返す）。
    tagsでフィルタリング可能（AND結合）。未指定で全件検索。

    Args:
        keyword: 検索キーワード（2文字以上）。配列で複数指定時はAND検索
        tags: タグフィルタ（AND条件。未指定=全件検索）
        type_filter: 検索対象の絞り込み（'topic', 'decision', 'activity', 'log', 'material'。未指定で全種類）
        limit: 取得件数上限（デフォルト10件、最大50件）
        offset: スキップ件数（デフォルト0）。ページネーション用
        keyword_mode: キーワード結合モード（"and" または "or"。デフォルト "and"）
        include_details: Trueのとき上位10件にdetailsを自動添付する（デフォルトFalse）

    Returns:
        検索結果一覧（type, id, title, score, snippet, tags）
        snippetは各typeの対応するソースカラムの先頭200文字（materialはtitle優先表示）。
        tagsはエンティティに紐づくタグ文字列のリスト。
        include_details=Trueの場合、上位10件にdetailsが追加される。
    """
    result = search_service.search(keyword, tags, type_filter, limit, offset, keyword_mode, include_details)
    if "error" not in result and tags:
        _maybe_inject_tag_notes(result, tags)
    return result


@mcp.tool()
def get_by_ids(
    items: list[dict],
) -> dict:
    """
    search結果の詳細情報を取得する。

    searchツールで得られたtype + idペアを指定して、
    各アイテムの全文を返す。1件でも複数件でも使える。

    Args:
        items: 取得対象のリスト。各要素は {type: str, id: int}（最大20件）
               type: データ種別（'topic', 'decision', 'activity', 'log', 'material'）
               id: データのID

    Returns:
        取得結果（各アイテムの詳細情報）
    """
    result = search_service.get_by_ids(items)
    if "error" not in result:
        all_tags = []
        for item in result.get("results", []):
            if "data" in item:
                all_tags.extend(item["data"].get("tags", []))
        if all_tags:
            _maybe_inject_tag_notes(result, all_tags)
    return result


@mcp.tool()
def list_tags(
    namespace: Optional[str] = None,
) -> dict:
    """
    タグ一覧をusage_count付きで取得する。

    タグの利用状況を確認するときに使う。
    namespaceでフィルタリング可能。
    エイリアスタグにはcanonicalフィールド（エイリアス先のタグ文字列）が含まれる。

    Args:
        namespace: namespaceでフィルタ（"domain", "intent", ""。未指定で全タグ）

    Returns:
        タグ一覧（tag, id, namespace, name, usage_count, notes, canonical）をusage_count降順で返す
    """
    return _list_tags(namespace)


@mcp.tool()
def update_tag(
    tag: str,
    notes: Optional[str] = None,
    canonical: Optional[str] = None,
    rename: Optional[str] = None,
) -> dict:
    """
    既存タグの notes（教訓・運用ルール）、canonical（エイリアス先）、またはname（リネーム）を更新する。

    notes / canonical / rename は相互排他（1つだけ指定可能）。少なくとも1つを指定する。

    notes: タグに紐づく教訓や運用ルールを記録する。CLAUDE.mdのタグ版として機能し、
    そのタグの文脈で作業するときに自動的にAIに注入される。上書き方式（全文置換）。

    canonical: エイリアス先タグを指定する。設定すると、tagがcanonicalのエイリアスになり、
    以降tagで記録・検索するとcanonical側のタグIDで解決される。
    設定時に既存の紐付け（topic_tags等4テーブル）をcanonical側に付け替える。
    この付け替えは設定時の1回のみで、canonical上書き時に旧付け替え分は戻らない。
    canonical=""で解除。連鎖（エイリアスのエイリアス）は禁止。
    notes付きタグはエイリアスにできない（先にnotesを除去すること）。

    rename: 新しいタグ名。namespace変更も可能（例: "hooks" → "domain:hooks"）。
    IDベースの参照なので紐付けはそのまま維持される。
    新名が既存タグと衝突する場合はエラー。

    Args:
        tag: 対象タグ（例: "domain:cc-memory", "hooks"）
        notes: 教訓・運用ルールのテキスト（全文置換）
        canonical: エイリアス先タグ（""で解除）
        rename: 新しいタグ名（例: "domain:hooks"）

    Returns:
        更新結果
    """
    return _update_tag(tag, notes=notes, canonical=canonical, rename=rename)


@mcp.tool()
def analyze_tags(
    domain: Optional[str] = None,
    include_domain_tags: bool = False,
    focus_tag: Optional[str] = None,
    min_usage: int = 2,
    top_n: int = 20,
) -> dict:
    """タグの共起分析を実行する。PMIで共起の重みを計算し、クラスタ検出・孤児タグ検出・重複候補検出を行う。

    Args:
        domain: domainフィルタ（例: "cc-memory"）。指定時はそのdomainに属するエンティティのみを分析対象にする
        include_domain_tags: Trueの場合、domain:タグも分析対象に含める（デフォルトFalse）
        focus_tag: 特定タグにフォーカス。指定時はco_occurrencesをそのタグを含むペアのみに絞る
        min_usage: 孤児判定の閾値。usage_countがこの値未満のタグを孤児とする（デフォルト2）
        top_n: co_occurrencesの返却件数上限（デフォルト20）

    Returns:
        co_occurrences: 共起ペア（PMI降順）
        clusters: PMI閾値ベースの連結成分クラスタ
        orphans: 使用頻度が低い孤児タグ
        suspected_duplicates: embedding類似度ベースの重複候補
    """
    return _analyze_tags(domain, include_domain_tags, focus_tag, min_usage, top_n)


@mcp.tool()
def add_activity(
    title: str,
    description: str,
    tags: list[str],
    related: list[dict] | None = None,
    check_in: bool = True,
) -> dict:
    """
    新しいアクティビティを追加する。デフォルトで作成後にcheck_inも実行する。

    典型的な使い方:
    - 作業アクティビティを作成: add_activity("○○機能を実装", "詳細説明...", ["domain:cc-memory", "intent:implement", "search", "ranking"])
    - トピック紐付け: add_activity("○○機能を実装", "詳細説明...", ["domain:cc-memory", "intent:implement"], related=[{"type": "topic", "ids": [123]}])
    - 複数関連: add_activity("...", "...", [...], related=[{"type": "topic", "ids": [1, 2]}, {"type": "activity", "ids": [3]}])
    - check_inなしで作成: add_activity("○○機能を実装", "詳細説明...", ["domain:cc-memory", "intent:implement"], check_in=False)

    Args:
        title: アクティビティのタイトル
        description: アクティビティの詳細説明（必須）
        tags: タグ配列（必須、1個以上）。domain:タグとintent:タグは必須。素タグも積極的に付けること。namespace: domain:(プロジェクト)/intent:(意図)/素タグ(キーワード)。例: ["domain:cc-memory", "intent:implement", "search", "ranking"]
        related: 関連エンティティ（optional）。[{"type": "topic"|"activity", "ids": [int, ...]}] 形式。作成と同時にリレーションを張る
        check_in: 作成後にcheck_inを実行するか（デフォルト: True）。Trueの場合、返り値にcheck_in_resultが含まれる

    Returns:
        作成されたアクティビティ情報（check_in=Trueの場合はcheck_in_resultにtag_notes等を含む）
    """
    result = activity_service.add_activity(
        title, description, tags, related=related, check_in=check_in,
    )
    if "error" not in result:
        # check_in=Trueの場合、check_in_resultにtag_notesが含まれるため
        # _maybe_inject_tag_notesは不要（二重注入防止）
        if not check_in:
            _maybe_inject_tag_notes(result, tags)
    return result


@mcp.tool()
def get_activities(
    tags: list[str] | None = None,
    status: str = "active",
    limit: int = 5,
) -> dict:
    """
    アクティビティ一覧を取得する（tagsでフィルタリング、statusでフィルタリング可能）。

    典型的な使い方:
    - 全アクティビティ確認: get_activities()
    - ドメイン指定: get_activities(["domain:cc-memory"])
    - 進行中のみ: get_activities(["domain:cc-memory"], status="in_progress")
    - 完了アクティビティの確認: get_activities(status="completed")

    ワークフロー位置: アクティビティ状況の確認時

    Args:
        tags: タグ配列（optional）。指定時はAND条件でフィルタ。未指定時は全件返す。例: ["domain:cc-memory"]
        status: フィルタするステータス（active/pending/in_progress/completed、デフォルト: active）
                "active"はpending+in_progressの両方を返すエイリアス
        limit: 取得件数上限（デフォルト: 5）

    Returns:
        アクティビティ一覧（total_countで該当ステータスの全件数を確認可能）
    """
    result = activity_service.get_activities(tags, status, limit)
    if "error" not in result and tags:
        _maybe_inject_tag_notes(result, tags)
    return result


@mcp.tool()
def update_activity(
    activity_id: int,
    new_status: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """
    アクティビティのステータス・タイトル・説明・タグを更新する。

    典型的な使い方:
    - アクティビティ開始: update_activity(activity_id, new_status="in_progress")
    - アクティビティ完了: update_activity(activity_id, new_status="completed")
    - タイトル変更: update_activity(activity_id, title="新しいタイトル")
    - 説明更新: update_activity(activity_id, description="新しい説明")
    - タグ変更: update_activity(activity_id, tags=["domain:cc-memory", "intent:implement"])

    ワークフロー位置: アクティビティ進行状況の更新時

    Args:
        activity_id: アクティビティID
        new_status: 新しいステータス（pending/in_progress/completed）
        title: 新しいタイトル
        description: 新しい説明
        tags: 新しいタグ配列（指定時は全置換。1個以上必須）

    Returns:
        更新されたアクティビティ情報
    """
    return activity_service.update_activity(activity_id, new_status, title, description, tags)


@mcp.tool()
def add_material(
    activity_id: int,
    title: str,
    content: str,
) -> dict:
    """
    アクティビティに紐づく資材を追加する。

    資材はアクティビティの成果物・ドキュメントをDB保存する仕組み。
    check-inツールからカタログとして参照され、全文はget_materialで取得する2段階リード設計。

    典型的な使い方:
    - 設計ドキュメントを保存: add_material(123, "API設計書", "# API設計\n...")
    - 調査結果を保存: add_material(123, "既存実装の調査結果", "## 調査結果\n...")

    Args:
        activity_id: 紐づくアクティビティのID（必須、存在するアクティビティIDを指定）
        title: 資材のタイトル
        content: 資材の本文（マークダウン形式推奨）

    Returns:
        作成された資材情報（material_id, activity_id, title, content, created_at）
    """
    return material_service.add_material(activity_id, title, content)


@mcp.tool()
def get_material(
    material_id: int,
) -> dict:
    """
    資材の全文を取得する。

    get_by_idsで取得したmaterial概要の詳細を取得する際に使う（2段階リードの後半）。

    Args:
        material_id: 資材のID

    Returns:
        資材の全文情報（material_id, activity_id, title, content, created_at）
    """
    return material_service.get_material(material_id)


@mcp.tool()
def list_materials(
    activity_id: int,
) -> dict:
    """
    アクティビティに紐づく資材のカタログ一覧を取得する。

    全文は含まない。詳細はget_materialで取得する（2段階リード設計）。

    Args:
        activity_id: アクティビティのID

    Returns:
        資材カタログ一覧（activity_id, materials[{material_id, activity_id, title, created_at}], total_count）
    """
    return material_service.list_materials(activity_id)


@mcp.tool()
def check_in(
    activity_id: int,
) -> dict:
    """
    アクティビティにcheck-inする。関連情報を集約取得しsummaryを返す。

    既存アクティビティに関連する作業を始めるときに呼ぶ。
    tag_notes・資材カタログ・関連decisionsを一括取得し、
    statusがin_progress以外なら自動的にin_progressに更新する。
    summaryフィールドをそのまま出力すること。

    Args:
        activity_id: アクティビティID

    Returns:
        check-in結果（activity, related_topics, related_activities, tag_notes, materials, recent_decisions, catalog, summary）
    """
    return _check_in(activity_id)



@mcp.tool()
def add_relation(
    source_type: str,
    source_id: int,
    targets: list[dict],
) -> dict:
    """
    エンティティ間のリレーションを追加する。

    典型的な使い方:
    - トピック同士を関連付け: add_relation("topic", 1, [{"type": "topic", "ids": [2, 3]}])
    - アクティビティとトピックを関連付け: add_relation("activity", 10, [{"type": "topic", "ids": [1]}])
    - 複数タイプを一度に: add_relation("topic", 1, [{"type": "topic", "ids": [2]}, {"type": "activity", "ids": [10, 11]}])

    Args:
        source_type: 起点エンティティのタイプ（"topic" or "activity"）
        source_id: 起点エンティティのID
        targets: ターゲットリスト [{"type": "topic"|"activity", "ids": [int, ...]}, ...]

    Returns:
        成功時: {"added": int}（実際に追加された件数。重複はカウントしない）
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    return relation_service.add_relation(source_type, source_id, targets)


@mcp.tool()
def remove_relation(
    source_type: str,
    source_id: int,
    targets: list[dict],
) -> dict:
    """
    エンティティ間のリレーションを削除する。

    Args:
        source_type: 起点エンティティのタイプ（"topic" or "activity"）
        source_id: 起点エンティティのID
        targets: ターゲットリスト [{"type": "topic"|"activity", "ids": [int, ...]}, ...]

    Returns:
        成功時: {"removed": int}（実際に削除された件数）
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    return relation_service.remove_relation(source_type, source_id, targets)


@mcp.tool()
def get_map(
    entity_type: str,
    entity_id: int,
    min_depth: int = 0,
    max_depth: int = 2,
) -> dict:
    """
    リレーショングラフを走査し、到達可能エンティティのカタログを返す。

    再帰的にリレーションを辿り、指定深度範囲のエンティティをカタログ形式で返す。
    check-in時の2次カタログと同じロジックを使用。

    Args:
        entity_type: 起点エンティティのタイプ（"topic" or "activity"）
        entity_id: 起点エンティティのID
        min_depth: 最小深度（デフォルト: 0。0=起点自身を含む）
        max_depth: 最大深度（デフォルト: 2、上限: 10）

    Returns:
        成功時: {"entities": [{"type", "id", "title", "tags", "depth"}, ...], "total_count": int}
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    return relation_service.get_map(entity_type, entity_id, min_depth, max_depth)


@mcp.tool()
def add_reminder(content: str) -> dict:
    """リマインダーを追加する。check-in時に自動注入される。"""
    return reminder_service.add_reminder(content)


@mcp.tool()
def list_reminders() -> dict:
    """リマインダー一覧を取得する。"""
    return reminder_service.list_reminders()


@mcp.tool()
def update_reminder(reminder_id: int, content: Optional[str] = None, active: Optional[int] = None) -> dict:
    """リマインダーを更新する。active=0で無効化、active=1で再有効化。"""
    return reminder_service.update_reminder(reminder_id, content=content, active=active)


@mcp.tool()
def roll_dice(sides: int = 10) -> dict:
    """指定面数のダイスを振る。デフォルト1d10。"""
    return {"result": random.randint(1, sides)}


# セッションエンドポイント（HTTPモード用カスタムルート）
@mcp.custom_route("/session/register", methods=["POST"])
async def session_register(request: Request) -> JSONResponse:
    """セッション登録エンドポイント"""
    mgr = get_session_manager()
    if mgr is None:
        return JSONResponse(
            {"error": "Session management not available (stdio mode)"},
            status_code=503,
        )
    try:
        body = await request.json()
        session_id = body.get("session_id")
        if not session_id or not isinstance(session_id, str):
            return JSONResponse(
                {"error": "session_id is required (string)"},
                status_code=400,
            )
        is_new = mgr.register(session_id)
        return JSONResponse({
            "registered": is_new,
            "active_sessions": mgr.active_count,
        })
    except Exception as e:
        logger.exception("session_register failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/session/unregister", methods=["POST"])
async def session_unregister(request: Request) -> JSONResponse:
    """セッション解除エンドポイント"""
    mgr = get_session_manager()
    if mgr is None:
        return JSONResponse(
            {"error": "Session management not available (stdio mode)"},
            status_code=503,
        )
    try:
        body = await request.json()
        session_id = body.get("session_id")
        if not session_id or not isinstance(session_id, str):
            return JSONResponse(
                {"error": "session_id is required (string)"},
                status_code=400,
            )
        removed = mgr.unregister(session_id)
        return JSONResponse({
            "unregistered": removed,
            "active_sessions": mgr.active_count,
        })
    except Exception as e:
        logger.exception("session_unregister failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# サーバー起動
from src.http_config import HTTP_HOST, HTTP_PORT


if __name__ == "__main__":
    import argparse
    import os
    import signal

    parser = argparse.ArgumentParser(description="cc-memory MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http"],
        help="トランスポート方式（デフォルト: stdio）",
    )
    args = parser.parse_args()

    from src.db import init_database
    init_database()

    if args.transport == "http":
        import socket
        from src.services.lock_file import acquire, release
        from src.services.session_manager import SessionManager

        # ポートの空き確認
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((HTTP_HOST, HTTP_PORT))
        except OSError:
            logger.error(f"Port {HTTP_PORT} is already in use")
            raise SystemExit(1)

        # ロックファイル取得
        if not acquire(HTTP_PORT):
            logger.error("Failed to acquire lock file. Another server may be running.")
            raise SystemExit(1)

        # セッションマネージャー初期化
        _session_manager = SessionManager()

        def _shutdown_server():
            """ウォッチドッグから呼ばれるシャットダウンハンドラ"""
            logger.info("Shutdown triggered by watchdog, sending SIGINT")
            os.kill(os.getpid(), signal.SIGINT)

        _session_manager.set_shutdown_callback(_shutdown_server)
        _session_manager.start_watchdog()

        try:
            logger.info(f"Starting HTTP server on {HTTP_HOST}:{HTTP_PORT}")
            mcp.run(transport="http", host=HTTP_HOST, port=HTTP_PORT)
        finally:
            release()
    else:
        mcp.run()
