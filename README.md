# claude-code-memory

Claude Codeのセッション間で、議論の文脈・決定事項・作業状況を永続化するプラグインです。

## 何が解決されるのか

Claude Codeはセッションごとに記憶がリセットされます。短いタスクなら問題ありませんが、長期プロジェクトでは「前に何を決めたか」「なぜその設計にしたか」「どこまで作業が進んでいるか」がセッションをまたぐと失われます。

claude-code-memoryは、こうした文脈をSQLiteデータベースに保存し、新しいセッションでAIが自動的に過去の記録を参照できるようにします。同じ説明を繰り返す必要がなくなり、議論の積み重ねがそのまま次のセッションに引き継がれます。

## 主な機能

- **トピック管理** — 議論の主題ごとに情報を整理します
- **決定事項の記録** — 合意した内容を理由とともに保存します
- **議論ログ** — 議論の経緯や検討過程を保存します
- **アクティビティ管理** — 作業タスクの進捗をステータスで追跡します
- **資材管理** — セッション中に生成された分析結果・ドラフト等をアクティビティに紐づけて永続化します
- **リレーション** — トピック・アクティビティ間の関連をグラフ構造で管理します
- **タグシステム** — トピック・決定・ログ・アクティビティを横断的にタグで分類します。タグにnotesを付けて作業開始時にAIへ自動注入できます
- **リマインダー** — check-in時にAIへ毎回注入される運用ルールを管理します
- **ハイブリッド検索** — キーワード検索（FTS5）とベクトル検索を組み合わせて関連情報を見つけます
- **自動同期** — セッション終了時にstop hookで自動起動し、会話内容をcc-memoryに同期します

## インストール

### 前提条件

- [uv](https://docs.astral.sh/uv/) がインストールされていること
- Claude Code v2.0.12以上

### インストール手順

```bash
# マーケットプレイスを追加
claude plugin marketplace add isizono/cc-memory

# プラグインをインストール
claude plugin install claude-code-memory
```

インストール後、Claude Code内で以下を実行すると使い方の案内が表示されます。

```
/guide
```

## MCPツール

| カテゴリ | ツール | 説明 |
|---------|--------|------|
| トピック | `add_topic`, `get_topics` | 議論トピックの作成・取得 |
| 議論ログ | `add_log`, `get_logs` | 議論の経緯や検討過程の記録・取得 |
| 決定事項 | `add_decision`, `get_decisions` | 合意内容の記録・取得 |
| アクティビティ | `add_activity`, `get_activities`, `update_activity` | 作業タスクの作成・取得・状態更新 |
| check-in | `check_in` | アクティビティにcheck-inし、tag notes・資材・関連decisionsを集約取得 |
| 資材 | `add_material`, `get_material` | セッション中の成果物をタグ付き独立エンティティとして保存・取得 |
| リレーション | `add_relation`, `remove_relation`, `get_map` | エンティティ間の関連の追加・削除・グラフ探索 |
| リマインダー | `add_reminder`, `list_reminders`, `update_reminder` | check-in時に注入される運用ルールの管理 |
| タグ | `list_tags`, `update_tag`, `analyze_tags` | タグ一覧の取得・タグ情報の更新・タグ共起分析 |
| 検索 | `search`, `get_by_ids` | キーワード横断検索・詳細情報の取得 |

## スキル

| スキル | 説明 |
|--------|------|
| `/guide` | cc-memoryの使い方をAIが説明します |
| `/sync-memory` | セッション終了前にtranscriptを解析し、トピック・決定事項・アクティビティを一括で記録・更新します |
| `/check-in` | アクティビティにcheck-inして関連情報を集約取得します |
| `/tag-notes` | タグのnotesを確認・更新します |
| `/tag-cleanup` | タグの共起分析を実行し、整理提案をユーザーに提示します |
| `/scribe` | cc-memoryの記録からドキュメントを生成します |
| `/postmortem` | completedアクティビティを振り返り、教訓を永続化します |

## 設定

### 環境変数

| 変数 | 説明 | デフォルト |
|------|------|-----------|
| `DISCUSSION_DB_PATH` | SQLiteデータベースのパス | `~/.claude/.claude-code-memory/discussion.db` |

## ライセンス

MIT
