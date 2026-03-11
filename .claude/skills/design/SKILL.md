---
name: design
description: This skill determines implementation approach and technical decisions. Use when How/Interface/Edge cases need decisions, or when working on a cc-memory activity with [設計] prefix or intent:design tag.
---

# 設計フェーズ Skill

## 目的

議論フェーズで明確になったWhat/Why/Scopeをもとに、具体的な方針・仕様を決定し、実装に必要な決定事項を揃える。

## 開始前チェック（必須）

**このSkillを実行する前に、必ず以下を確認する：**

1. 関連する `[議論]` アクティビティを探す
2. そのアクティビティに紐づくdecision（What/Why/Scope）があるか確認
3. **What/Why/Scopeを確認できるdecisionがない場合**:
   - ユーザーに「まず議論が必要」と伝える
   - 議論フェーズへ誘導する
   - **このSkillの処理は中断する**

```
例: 「トピック検索機能」の設計を始める場合

1. アクティビティ一覧から [議論] アクティビティを探す
   get_activities(subject_id=2)
   → { activities: [{ id: 38, title: "[議論] トピック検索機能の要件整理", topic_id: 85, ... }] }

2. そのアクティビティに紐づくトピックの決定事項を確認
   get_decisions(topic_id=85)
   → {
       decisions: [{
         decision: "【What】トピックをキーワードで検索できるようにする
                    【Why】トピック数が増えると目的のトピックを見つけにくくなるため
                    【Scope】title/descriptionの部分一致検索。全文検索や曖昧検索はやらない
                    【Acceptance】「hooks」で検索したらhooks関連のトピックが出てくる",
         reason: "..."
       }]
     }

3. What/Why/Scope/Acceptanceが揃っている → 設計フェーズへ進める
   ※ 不足している場合は議論フェーズへ戻す
```

## 開始時のアクション

開始前チェックをパスしたら：

1. intent:design タグ付きアクティビティで起動された場合、そのアクティビティを対象とする
2. それ以外の場合、既存の `[設計]` アクティビティを探す（議論フェーズで作成済みの場合がある）
   - あれば → そのアクティビティを引き継ぐ（新規作成しない）
   - なければ → `[設計]` アクティビティを新規作成する
3. 議論フェーズのdecisionを確認し、前提を共有する

```
例: 「トピック検索機能」の設計を開始

1. 既存の設計アクティビティを探す
   get_activities(subject_id=2, status="pending") および get_activities(subject_id=2, status="in_progress")
   → [設計] アクティビティがあればそれを使う。なければ作成:
   add_activity(
       subject_id=2,
       title="[設計] トピック検索機能の方針決定",
       description="議論フェーズで決まったWhat/Why/Scope/Acceptanceをもとに、How/Interface/Edge cases/Verificationを決める"
   )

2. 議論フェーズの決定事項をユーザーに共有
   「議論フェーズで以下が決まってるね：
   - What: トピックをキーワードで検索できるようにする
   - Why: トピック数が増えると目的のトピックを見つけにくくなるため
   - Scope: title/descriptionの部分一致検索。全文検索や曖昧検索はやらない
   - Acceptance: 「hooks」で検索したらhooks関連のトピックが出てくる

   この前提で設計進めていい？」
```

## 完了条件

実装に必要な決定事項が揃っていること：

- **How**: どう実現するか（技術選定、アーキテクチャ、アプローチ）
- **Interface**: 外部とのやり取り（API、UI、データ形式など）
- **Edge cases**: 考慮すべきエッジケース・エラーハンドリング
- **Verification**: 最低限の動作保証項目
- **Non-functional**: 性能要件、セキュリティ要件（該当する場合）

### 具体例

```
例: 「トピック検索機能」の設計

前提（議論フェーズで決定済み）:
- What: トピックをキーワードで検索できるようにする
- Why: トピック数が増えると目的のトピックを見つけにくくなるため
- Scope: title/descriptionの部分一致検索。全文検索や曖昧検索はやらない
- Acceptance: 「hooks」で検索したらhooks関連のトピックが出てくる

【How】
- SQLiteのLIKE句を使用（%keyword%形式）
- 検索対象カラム: title, description
- 大文字小文字: SQLiteデフォルトのCOLLATE NOCASEで区別しない
- 検索ロジック: titleまたはdescriptionのいずれかにマッチすればヒット
- 結果の並び順: created_at DESC（新しい順）

【Interface】
- MCPツール: search_topics(subject_id: int, keyword: str, limit: int = 30)
- 戻り値: { topics: [{ id, title, description, parent_topic_id, created_at }, ...] }
- エラー時: MCPの標準エラー形式で返す

【Edge cases】
- keywordが空文字 → エラーを返す（全件取得はget_topicsを使う）
- keywordが1文字 → 許可する（ただし結果が多くなる可能性あり）
- 該当なし → 空配列を返す（エラーではない）
- keywordに%や_が含まれる → エスケープしてリテラル検索する
- subject_idが存在しない → 空配列を返す（エラーではない）

【Verification】
- 正常系
  - 「hook」で検索 → 「Stopフック実装」「PostToolUseフック」等がヒット
  - 「HOOK」で検索 → 同じ結果（大文字小文字無視の確認）
  - descriptionに「自動記録」を含むトピック → 「自動記録」で検索してヒット
- 異常系
  - 空文字で検索 → エラーが返る
  - 存在しないsubject_id=9999で検索 → 空配列が返る
- エッジケース
  - 「%」で検索 → %を含むトピックのみヒット（ワイルドカードとして解釈されない）
  - limit=1で検索 → 1件だけ返る
```

## エージェントの振る舞い

### 基本姿勢

**トレードオフを明示**して、ユーザーに選ばせる。エージェントが勝手に決めない。

### やること

- **代替案を提示する**: 「A案とB案があるけど、どっちがいい？」
- **トレードオフを説明する**: 「A案は〇〇がメリットだけど、△△がデメリット」
- **影響範囲を確認する**: 「これ変えると、〇〇にも影響あるけど大丈夫？」
- **抜け漏れを指摘する**: 「〇〇のケース考慮してなくない？」

### 根拠のルール

**1次情報ベース**で判断する。

- 公式ドキュメント・ベストプラクティスや、実際のコードベースなどを根拠にする
- 信頼度の高いソースを示せないのは原則NG

#### 1次情報の定義

以下を1次情報として扱う：
- 公式ドキュメント（公式サイト、GitHub READMEなど）
- コードベース自体（実装、テスト、コメント）
- 公式のRFC、仕様書、設計ドキュメント
- ライブラリメンテナの公式ブログ・発表資料

以下は2次情報（参考程度）：
- 技術ブログ、Qiita、Medium記事
- Stack Overflow（ただし公式メンテナの回答は準1次扱い）
- 書籍（ただし著者が仕様策定者の場合は準1次扱い）

## 成果物

設計で決まった内容はデシジョン（decision）として記録する。
そのうえで、作業に必要な情報を作業アクティビティ（`[作業]` プレフィックス付き）として作成する。
作業アクティビティにはHow/Interface/Edge cases/Verification等の背景情報をなるべく詳しく書くこと。
特にEdge casesは網羅的に記載すること（実装者が判断に迷わないレベルまで）。
作業は別のAIが担う可能性が高く、原則としてアクティビティの情報だけを見て仕事をする。

```
例: 「トピック検索機能」の設計完了時

1. デシジョンを記録（合意内容と理由）
add_decision(
    topic_id=85,
    decision="トピック検索はSQLiteのLIKE句で実装する。検索対象はtitle/description、大文字小文字区別なし。全文検索（FTS5等）はスコープ外。",
    reason="LIKE句はシンプルで十分な性能。現時点のトピック数では全文検索エンジンは過剰。"
)

2. 作業アクティビティを作成（背景情報を詳しく）
add_activity(
    subject_id=<該当サブジェクトID>,
    title="[作業] トピック検索機能",
    description="""
## 背景
- 関連topic: id:85, decisions: id:XXX

## 仕様
【How】
- SQLiteのLIKE句を使用（%keyword%形式）
- 検索対象カラム: title, description
- 大文字小文字: COLLATE NOCASEで区別しない
- 結果の並び順: created_at DESC

【Interface】
- MCPツール: search_topics(subject_id: int, keyword: str, limit: int = 30)
- 戻り値: { topics: [{ id, title, description, parent_topic_id, created_at }, ...] }

【Edge cases】
- keywordが空文字 → エラーを返す（全件取得はget_topicsを使う）
- keywordが1文字 → 許可する（ただし結果が多くなる可能性あり）
- 該当なし → 空配列を返す（エラーではない）
- keywordに%や_が含まれる → エスケープしてリテラル検索する
- subject_idが存在しない → 空配列を返す（エラーではない）

【Verification】
- 「hook」で検索 → hooks関連トピックがヒット
- 「HOOK」で検索 → 同じ結果（大文字小文字無視）
- 空文字で検索 → エラー
"""
)
```

## フェーズ完了

完了条件を満たしたら、チェックリストの項目を実行し、設計の結果をユーザーに報告して終了する。
**作業フェーズは自動で開始しない。** ユーザーが明示的に作業を依頼するまで待つ。

### 完了時チェックリスト

以下をすべて満たしていることを確認する：

- [ ] 必須項目（How/Interface/Edge cases/Verification）がすべて明確になっている
- [ ] 該当する場合、Non-functional要件も記録されている
- [ ] ユーザーの承認を得ている
- [ ] add_decision()で合意内容を記録済み
- [ ] add_activity()で作業アクティビティ（`[作業]`プレフィックス付き）を作成済み

## フェーズの巻き戻し

設計中に議論で重要な要件が漏れていたことが判明した場合：

1. 設計アクティビティをブロック状態にする
2. 議論アクティビティを再開する（または新規作成）
3. 問題を解決してから設計に戻る
4. 巻き戻しの経緯をdecisionに記録する