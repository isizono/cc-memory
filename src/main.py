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
)
from src.services.checkin_service import check_in as _check_in
from src.services.tag_service import list_tags as _list_tags, update_tag as _update_tag, collect_tag_notes_for_injection
from src.db import execute_query, get_connection, row_to_dict

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


def _get_active_activities_by_tag(tag_id: int) -> list[dict]:
    """domain:タグに紐づくホットアクティビティ（pending + in_progress）を取得する。

    Args:
        tag_id: タグID

    Returns:
        [{"id": int, "title": str, "status": str}, ...]（in_progress優先、updated_at降順）
    """
    rows = execute_query(
        """
        SELECT a.id, a.title, a.status
        FROM activities a
        JOIN activity_tags at ON a.id = at.activity_id
        WHERE at.tag_id = ?
          AND a.status IN ('in_progress', 'pending')
        ORDER BY CASE a.status WHEN 'in_progress' THEN 0 ELSE 1 END,
                 a.updated_at DESC
        """,
        (tag_id,),
    )
    return [row_to_dict(r) for r in rows]


def _get_recent_non_domain_tags() -> list[str]:
    """直近7日で使われたdomain:以外のタグをフラット列挙する。

    topic_tags経由でトピックの作成日が直近7日のタグを取得する。

    Returns:
        ["intent:design", "intent:discuss", "hooks", ...]（使用頻度降順）
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

    domain:タグごとに旧subject形式を再現（最新トピック3件 + ホットアクティビティ一覧）し、
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
            activities = _get_active_activities_by_tag(tag_id)

            # トピックもアクティビティもなければスキップ
            if not topics and not activities:
                continue

            lines = [f"## {name} (domain)"]

            if topics:
                lines.append("最新トピック:")
                for t in topics:
                    desc = _truncate_desc(t["description"])
                    desc_part = f": {desc}" if desc else ""
                    lines.append(f"- [{t['id']}] {t['title']}{desc_part}")

            if activities:
                lines.append("ホットアクティビティ:")
                for t in activities:
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
フェーズを混ぜないでください。現在のフェーズを完了し、ユーザーの確認を得てから次に進みます。
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

記録には、何に合意したかだけでなく、なぜそうしたかも含めてください。
設計判断、技術選定、スコープ境界、命名規約、トレードオフの解決など、
合意したことは何でも対象になります。
ただし、一方的に記録しないでください。双方が納得していることが前提です。

書くときは具体的に。「適宜」「必要に応じて」のような曖昧な表現は避けて、
具体的な条件と値を使ってください。
合意がフォローアップ作業を伴う場合は、積極的にアクティビティも作成してください。

## ログの記録

決定事項は結論を記録しますが、そこに至る経緯は残りません。
ログはその経緯を保存するためのものです。
普段は見返されないかもしれませんが、いざ必要になったときに
そのトピックについての詳細な情報が失われていないことがとても重要です。

ログを取るかどうか、どのタイミングで取るか、どの程度の詳細度で取るかの判断基準はないので、あなたの判断に委ねるしかありませんが、
その判断が及ぼす影響は思っているより大きいです。大事にしてください。
ユーザーとの会話が濃ければ毎ターン詳細に取ってください。
セッション内で結論にたどり着けないケースも多いことに注意してください。
ツールのコール回数が多くなってコンテキストの圧迫に繋がっても、将来その議論を繰り返さなくて済むなら確実にユーザーとあなたにとって大きなメリットになります。

望ましい記録の流れはこうです：
ユーザーのプロンプトに対してレスポンスを生成し、stopする直前に
ログ（ユーザー：〜 / エージェント：〜）を残すイメージです。
採用しなかった選択肢も含めてください。

### 資材（material）

セッション中に生成された情報（ドラフト、分析結果、SA出力など）はセッション終了とともに消えます。
これらの生情報は要約せず、`add_material`で資材として保存してください。
資材はアクティビティに紐づくため、logには資材のIDと概要だけを記載します。
（もし紐づけるべきアクティビティが存在しないと感じたのなら、その時点で会話を振り返って、現在どんなアクティビティの実行中かを確認してcheck-inしてください。）

例えば、「SA案を採択した」というdecisionだけ残しても、SA案そのものはセッション終了で揮発します。
次のセッションでは何を採択したのかわからず、結局議論のやり直しになります。
資材として保存しておけば、check-in時にカタログとして一覧され、`get_material`で全文取得できます。

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
    """ルール + アクティブコンテキストを組み立てる"""
    context = _build_active_context()
    if context:
        return f"{RULES}\n{context}"
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


# MCPツール定義
@mcp.tool()
def add_topic(
    title: str,
    description: str,
    tags: list[str],
) -> dict:
    """新しい議論トピックを追加する。

    tags: タグ配列（必須、1個以上）。domain:タグに加えて内容を表すタグも付けること。namespace: domain:(プロジェクト)/intent:(意図)/素タグ(キーワード)。例: ["domain:cc-memory", "intent:implement", "error-handling", "validation", "stdin"]
    """
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
        type_filter: 検索対象の絞り込み（'topic', 'decision', 'activity', 'log'。未指定で全種類）
        limit: 取得件数上限（デフォルト10件、最大50件）
        offset: スキップ件数（デフォルト0）。ページネーション用
        keyword_mode: キーワード結合モード（"and" または "or"。デフォルト "and"）

    Returns:
        検索結果一覧（type, id, title, score, snippet, tags）
        snippetは各typeの対応するソースカラムの先頭200文字。
        tagsはエンティティに紐づくタグ文字列のリスト。
    """
    result = search_service.search(keyword, tags, type_filter, limit, offset, keyword_mode)
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
) -> dict:
    """
    既存タグの notes（教訓・運用ルール）またはcanonical（エイリアス先）を更新する。

    notesとcanonicalは排他（同時指定不可）。少なくとも1つを指定する。

    notes: タグに紐づく教訓や運用ルールを記録する。CLAUDE.mdのタグ版として機能し、
    そのタグの文脈で作業するときに自動的にAIに注入される。上書き方式（全文置換）。

    canonical: エイリアス先タグを指定する。設定すると、tagがcanonicalのエイリアスになり、
    以降tagで記録・検索するとcanonical側のタグIDで解決される。
    設定時に既存の紐付け（topic_tags等4テーブル）をcanonical側に付け替える。
    この付け替えは設定時の1回のみで、canonical上書き時に旧付け替え分は戻らない。
    canonical=""で解除。連鎖（エイリアスのエイリアス）は禁止。
    notes付きタグはエイリアスにできない（先にnotesを除去すること）。

    Args:
        tag: 対象タグ（例: "domain:cc-memory", "hooks"）
        notes: 教訓・運用ルールのテキスト（全文置換）
        canonical: エイリアス先タグ（""で解除）

    Returns:
        更新結果
    """
    return _update_tag(tag, notes=notes, canonical=canonical)


@mcp.tool()
def add_activity(
    title: str,
    description: str,
    tags: list[str],
    topic_id: int | None = None,
    check_in: bool = True,
) -> dict:
    """
    新しいアクティビティを追加する。デフォルトで作成後にcheck_inも実行する。

    典型的な使い方:
    - 作業アクティビティを作成: add_activity("○○機能を実装", "詳細説明...", ["domain:cc-memory", "intent:implement", "search", "ranking"])
    - トピック紐付け: add_activity("○○機能を実装", "詳細説明...", ["domain:cc-memory", "intent:implement"], topic_id=123)
    - check_inなしで作成: add_activity("○○機能を実装", "詳細説明...", ["domain:cc-memory", "intent:implement"], check_in=False)

    Args:
        title: アクティビティのタイトル
        description: アクティビティの詳細説明（必須）
        tags: タグ配列（必須、1個以上）。domain:タグとintent:タグは必須。素タグも積極的に付けること。namespace: domain:(プロジェクト)/intent:(意図)/素タグ(キーワード)。例: ["domain:cc-memory", "intent:implement", "search", "ranking"]
        topic_id: 関連トピックID（optional）。指定するとcheck_in時にrecent_decisionsが取得される
        check_in: 作成後にcheck_inを実行するか（デフォルト: True）。Trueの場合、返り値にcheck_in_resultが含まれる

    Returns:
        作成されたアクティビティ情報（check_in=Trueの場合はcheck_in_resultにtag_notes等を含む）
    """
    result = activity_service.add_activity(
        title, description, tags, topic_id=topic_id, check_in=check_in,
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
        check-in結果（activity, topic（topic_idがある場合のみ）, tag_notes, materials, recent_decisions, summary）
    """
    return _check_in(activity_id)



@mcp.tool()
def roll_dice(sides: int = 10) -> dict:
    """指定面数のダイスを振る。デフォルト1d10。"""
    return {"result": random.randint(1, sides)}


if __name__ == "__main__":
    from src.db import init_database
    init_database()
    mcp.run()
