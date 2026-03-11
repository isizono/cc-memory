-- Migration 014: scope:/mode: → intent: 名前空間統合
--
-- depends: 0013_add_materials
--
-- 変更内容:
--   - scope:タグを素タグに降格（重複時はjunction tableの参照をマージ）
--   - mode:タグをintent:に変換
--   - intent:初期タグの投入
--   - tagsテーブルのCHECK制約を ('', 'domain', 'intent') に更新

-- ============================================
-- Step 1: scope:タグの素タグ化（重複処理）
-- ============================================

-- 1a: 素タグと重複するscope:タグについて、4つのjunction tableの参照を付け替え
--     重複 = tags に (scope, X) と ('', X) の両方が存在するケース
--     OR IGNORE で、既に素タグ側にも紐付いているエンティティはスキップ

UPDATE OR IGNORE topic_tags
SET tag_id = (SELECT p.id FROM tags p WHERE p.namespace = '' AND p.name =
  (SELECT s.name FROM tags s WHERE s.id = topic_tags.tag_id))
WHERE tag_id IN (
  SELECT s.id FROM tags s
  JOIN tags p ON p.namespace = '' AND p.name = s.name
  WHERE s.namespace = 'scope'
);

UPDATE OR IGNORE decision_tags
SET tag_id = (SELECT p.id FROM tags p WHERE p.namespace = '' AND p.name =
  (SELECT s.name FROM tags s WHERE s.id = decision_tags.tag_id))
WHERE tag_id IN (
  SELECT s.id FROM tags s
  JOIN tags p ON p.namespace = '' AND p.name = s.name
  WHERE s.namespace = 'scope'
);

UPDATE OR IGNORE log_tags
SET tag_id = (SELECT p.id FROM tags p WHERE p.namespace = '' AND p.name =
  (SELECT s.name FROM tags s WHERE s.id = log_tags.tag_id))
WHERE tag_id IN (
  SELECT s.id FROM tags s
  JOIN tags p ON p.namespace = '' AND p.name = s.name
  WHERE s.namespace = 'scope'
);

UPDATE OR IGNORE activity_tags
SET tag_id = (SELECT p.id FROM tags p WHERE p.namespace = '' AND p.name =
  (SELECT s.name FROM tags s WHERE s.id = activity_tags.tag_id))
WHERE tag_id IN (
  SELECT s.id FROM tags s
  JOIN tags p ON p.namespace = '' AND p.name = s.name
  WHERE s.namespace = 'scope'
);

-- 1b: OR IGNOREでスキップされた行（既に素タグ側に紐付いていた）を削除
DELETE FROM topic_tags WHERE tag_id IN (
  SELECT s.id FROM tags s
  JOIN tags p ON p.namespace = '' AND p.name = s.name
  WHERE s.namespace = 'scope'
);
DELETE FROM decision_tags WHERE tag_id IN (
  SELECT s.id FROM tags s
  JOIN tags p ON p.namespace = '' AND p.name = s.name
  WHERE s.namespace = 'scope'
);
DELETE FROM log_tags WHERE tag_id IN (
  SELECT s.id FROM tags s
  JOIN tags p ON p.namespace = '' AND p.name = s.name
  WHERE s.namespace = 'scope'
);
DELETE FROM activity_tags WHERE tag_id IN (
  SELECT s.id FROM tags s
  JOIN tags p ON p.namespace = '' AND p.name = s.name
  WHERE s.namespace = 'scope'
);

-- 1c: 重複していたscope:タグレコード自体を削除
DELETE FROM tags WHERE namespace = 'scope' AND name IN (
  SELECT name FROM tags WHERE namespace = ''
);

-- 1d: 残りのscope:タグを素タグに変換（重複なし）
UPDATE tags SET namespace = '' WHERE namespace = 'scope';

-- ============================================
-- Step 2: tagsテーブル再作成（CHECK制約更新 + mode:→intent:変換）
-- ============================================
-- CHECK制約を先に更新しないと、後続のUPDATE/INSERTが制約違反になる。
-- mode:→intent:の変換はコピー時にCASE WHENで同時に行う。

CREATE TABLE tags_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace TEXT NOT NULL DEFAULT '' CHECK(namespace IN ('', 'domain', 'intent')),
  name TEXT NOT NULL,
  notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(namespace, name)
);

INSERT INTO tags_new (id, namespace, name, notes, created_at)
SELECT id,
  CASE WHEN namespace = 'mode' THEN 'intent' ELSE namespace END,
  name, notes, created_at
FROM tags;

DROP TABLE tags;
ALTER TABLE tags_new RENAME TO tags;

-- ============================================
-- Step 3: intent:初期タグの投入
-- ============================================

INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'design');
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'discuss');
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'investigate');
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'implement');
INSERT OR IGNORE INTO tags (namespace, name) VALUES ('intent', 'review');
