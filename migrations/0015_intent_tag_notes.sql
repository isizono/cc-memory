-- Migration 015: intent:タグに notes（振る舞いガイド）を投入
--
-- depends: 0014_intent_namespace

-- Step 1: 不足しているintent:タグを追加（既存なら無視）
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'discuss');
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'design');
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'investigate');
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'implement');
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'review');

-- Step 2: notesを投入
UPDATE tags SET notes = '境界: 設計・実装に入らない。

目的: ユーザーの要件から曖昧さを取り除き、決定事項として記録する。
完了条件: What / Why / Scope / Acceptance が明確になっていること。

振る舞い:
- ユーザーに言語化させることがゴール。エージェントが勝手に決めない
- 矛盾を指摘する。必要性を問う。代替案を出す。「なんで？」「具体的には？」で深掘りする
- 根拠は1次情報ベース（公式ドキュメント、コードベース等）。「一般的には〜」はNG
- 発散優先。本題から逸れなければどんどん広げていい。収束は急がない
- 先回りで関連情報を調べるのはOK。趣味ベースで調べ始めてもOK
- 成果物: decisionとして記録。設計フェーズは自動で開始しない'
WHERE namespace = 'intent' AND name = 'discuss';

UPDATE tags SET notes = '前提: 議論フェーズのdecision（What/Why/Scope）が揃っていること。なければ議論に戻す。
境界: 実装に入らない。

目的: How / Interface / Edge cases を決定し、実装に必要な決定事項を揃える。
完了条件: How / Interface / Edge cases / Verification が明確になっていること。

振る舞い:
- トレードオフを明示して、ユーザーに選ばせる。エージェントが勝手に決めない
- 代替案を提示する。影響範囲を確認する。抜け漏れを指摘する
- 根拠は1次情報ベース
- 成果物: decisionと[作業]アクティビティ。作業アクティビティには背景を詳しく書く
  （特にEdge casesは実装者が判断に迷わないレベルまで網羅）
- 作業フェーズは自動で開始しない'
WHERE namespace = 'intent' AND name = 'design';

UPDATE tags SET notes = '境界: 実装しない。

目的: 調査・情報収集に専念する。
振る舞い:
- 事実と推測を明確に分ける
- 調査結果はmaterialとして保存する'
WHERE namespace = 'intent' AND name = 'investigate';

UPDATE tags SET notes = '振る舞い:
- 着手前にアクティビティの仕様と関連decisionを確認する
- 完了したらユーザーの承認を得てからアクティビティを閉じる'
WHERE namespace = 'intent' AND name = 'implement';

UPDATE tags SET notes = '境界: 変更しない。指摘のみ。

振る舞い:
- diffだけ見て推測で指摘しない。実コードで呼び出し関係を確認してから指摘する'
WHERE namespace = 'intent' AND name = 'review';
