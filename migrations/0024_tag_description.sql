-- Migration 0024: tags.descriptionカラム追加 + intent:debug新設
--
-- depends: 0023_material_independent_entity

-- Step 1: descriptionカラム追加
ALTER TABLE tags ADD COLUMN description TEXT DEFAULT NULL
  CHECK(description IS NULL OR LENGTH(description) <= 100);

-- Step 2: intent:debugタグ新設
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'debug');

-- Step 3: intent:debugのnotes設定
UPDATE tags SET notes = '境界: 修正しない。原因特定まで。

目的: バグ・インシデントの根本原因を特定する。
完了条件: 原因が事実ベースで特定され、修正方針が提示されていること。

振る舞い:
- ログなどの情報を徹底確認する — まず手元にある証拠を全部集める
- サンドボックス環境で再現を試みる — 本番を触らず、隔離環境で検証
- 一ステップずつ進む — 飛躍せず段階的に
- 仮説は事実ベースで立てる — なぜそう思ったか・どう検証するかを、裏付けが取れた事実から始める
- 調査結果はmaterialとして保存する
- 推測で修正に走らない'
WHERE namespace = 'intent' AND name = 'debug';

-- Step 4: 6件のintentにdescription初期値設定
UPDATE tags SET description = '会話を通じて要件の曖昧さを取り除き、What/Why/Scopeを決定事項として記録する'
WHERE namespace = 'intent' AND name = 'discuss';

UPDATE tags SET description = 'How/Interface/Edge casesを決定し、実装に必要な仕様を揃える'
WHERE namespace = 'intent' AND name = 'design';

UPDATE tags SET description = '仕様とdecisionに基づいて実装する'
WHERE namespace = 'intent' AND name = 'implement';

UPDATE tags SET description = '調査・情報収集に専念し、結果をmaterialとして保存する'
WHERE namespace = 'intent' AND name = 'investigate';

UPDATE tags SET description = 'コードの差分を実コードと照合して指摘する。変更はしない'
WHERE namespace = 'intent' AND name = 'review';

UPDATE tags SET description = 'バグ・インシデントの原因を特定する。証拠収集→再現→段階的検証→事実ベース仮説で進める'
WHERE namespace = 'intent' AND name = 'debug';

-- Step 5: intent:investigateのnotes更新
UPDATE tags SET notes = '境界: 実装・修正しない。調査と情報整理まで。

目的: 技術リサーチ・仕様調査・比較検討など、情報収集と整理に専念する。
完了条件: 調査対象について事実ベースの情報が整理され、materialとして保存されていること。

振る舞い:
- 事実と推測を明確に分ける
- 調査結果はmaterialとして保存する
- 1次情報（公式ドキュメント、コードベース）を優先する
- バグ・インシデントの原因調査はintent:debugを使う'
WHERE namespace = 'intent' AND name = 'investigate';
