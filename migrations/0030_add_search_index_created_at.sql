-- Migration 030: search_indexにcreated_atカラムを追加
--
-- depends: 0029_add_pinned
--
-- 背景:
--   構造化クエリ（日付フィルタ）を実現するため、search_indexに
--   created_atカラムを追加する。既存レコードにはソーステーブルの
--   created_atをバックフィルし、INSERTトリガーを更新する。
--
-- 変更内容:
--   1. ALTER TABLE search_index ADD COLUMN created_at
--   2. 5種エンティティのバックフィル
--   3. 5本のINSERTトリガーをDROP→再CREATE（created_at含む）

-- ================================================
-- Step 1: カラム追加
-- ================================================

ALTER TABLE search_index ADD COLUMN created_at TEXT NOT NULL DEFAULT '';

-- ================================================
-- Step 2: バックフィル（5種エンティティ）
-- ================================================

UPDATE search_index SET created_at = (
  SELECT t.created_at FROM discussion_topics t WHERE t.id = search_index.source_id
) WHERE source_type = 'topic';

UPDATE search_index SET created_at = (
  SELECT d.created_at FROM decisions d WHERE d.id = search_index.source_id
) WHERE source_type = 'decision';

UPDATE search_index SET created_at = (
  SELECT a.created_at FROM activities a WHERE a.id = search_index.source_id
) WHERE source_type = 'activity';

UPDATE search_index SET created_at = (
  SELECT dl.created_at FROM discussion_logs dl WHERE dl.id = search_index.source_id
) WHERE source_type = 'log';

UPDATE search_index SET created_at = (
  SELECT m.created_at FROM materials m WHERE m.id = search_index.source_id
) WHERE source_type = 'material';

-- ================================================
-- Step 3: INSERTトリガーをDROP→再CREATE（created_at追加）
-- ================================================

-- topics
DROP TRIGGER IF EXISTS trg_search_topics_insert;
CREATE TRIGGER IF NOT EXISTS trg_search_topics_insert
AFTER INSERT ON discussion_topics
BEGIN
  INSERT INTO search_index (source_type, source_id, title, created_at)
  VALUES ('topic', NEW.id, NEW.title, NEW.created_at);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.description);
END;

-- decisions
DROP TRIGGER IF EXISTS trg_search_decisions_insert;
CREATE TRIGGER IF NOT EXISTS trg_search_decisions_insert
AFTER INSERT ON decisions
BEGIN
  INSERT INTO search_index (source_type, source_id, title, created_at)
  VALUES ('decision', NEW.id, NEW.decision, NEW.created_at);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.decision, NEW.reason);
END;

-- activities
DROP TRIGGER IF EXISTS trg_search_activities_insert;
CREATE TRIGGER IF NOT EXISTS trg_search_activities_insert
AFTER INSERT ON activities
BEGIN
  INSERT INTO search_index (source_type, source_id, title, created_at)
  VALUES ('activity', NEW.id, NEW.title, NEW.created_at);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.description);
END;

-- logs
DROP TRIGGER IF EXISTS trg_search_logs_insert;
CREATE TRIGGER IF NOT EXISTS trg_search_logs_insert
AFTER INSERT ON discussion_logs
BEGIN
  INSERT INTO search_index (source_type, source_id, title, created_at)
  VALUES ('log', NEW.id, NEW.title, NEW.created_at);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.content);
END;

-- materials
DROP TRIGGER IF EXISTS trg_search_materials_insert;
CREATE TRIGGER IF NOT EXISTS trg_search_materials_insert
AFTER INSERT ON materials
BEGIN
  INSERT INTO search_index (source_type, source_id, title, created_at)
  VALUES ('material', NEW.id, NEW.title, NEW.created_at);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.content);
END;
