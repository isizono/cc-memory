-- Migration 010: subject廃止とトリガー書き直し（Contract）
--
-- depends: 0009_tag_infrastructure
--
-- 変更内容:
--   - 既存トリガー12個をDROP
--   - subject_id関連インデックス4個を削除
--   - discussion_topics.subject_id, parent_topic_id カラム削除
--   - tasks.subject_id, topic_id カラム削除
--   - search_index.subject_id カラム削除
--   - subjects テーブル削除
--   - トリガー12個をsubject_id参照を除去した形で再作成

-- ============================================
-- Step 1: 既存トリガー12個をDROP
-- ============================================

DROP TRIGGER IF EXISTS trg_search_topics_insert;
DROP TRIGGER IF EXISTS trg_search_topics_update;
DROP TRIGGER IF EXISTS trg_search_topics_delete;
DROP TRIGGER IF EXISTS trg_search_decisions_insert;
DROP TRIGGER IF EXISTS trg_search_decisions_update;
DROP TRIGGER IF EXISTS trg_search_decisions_delete;
DROP TRIGGER IF EXISTS trg_search_tasks_insert;
DROP TRIGGER IF EXISTS trg_search_tasks_update;
DROP TRIGGER IF EXISTS trg_search_tasks_delete;
DROP TRIGGER IF EXISTS trg_search_logs_insert;
DROP TRIGGER IF EXISTS trg_search_logs_update;
DROP TRIGGER IF EXISTS trg_search_logs_delete;

-- ============================================
-- Step 2: インデックス4個を削除
-- ============================================

DROP INDEX IF EXISTS idx_topics_subject_id;
DROP INDEX IF EXISTS idx_topics_parent_id;
DROP INDEX IF EXISTS idx_tasks_subject_id;
DROP INDEX IF EXISTS idx_search_index_subject;

-- ============================================
-- Step 3: カラム5個を削除
-- ============================================

ALTER TABLE discussion_topics DROP COLUMN subject_id;
ALTER TABLE discussion_topics DROP COLUMN parent_topic_id;
ALTER TABLE tasks DROP COLUMN subject_id;
ALTER TABLE tasks DROP COLUMN topic_id;
ALTER TABLE search_index DROP COLUMN subject_id;

-- ============================================
-- Step 4: subjectsテーブル削除
-- ============================================

DROP TABLE IF EXISTS subjects;

-- ============================================
-- Step 5: 新トリガー12個を作成
-- subject_id参照を除去した版。ロジック自体は変更なし。
-- ============================================

-- discussion_topics

CREATE TRIGGER IF NOT EXISTS trg_search_topics_insert
AFTER INSERT ON discussion_topics
BEGIN
  INSERT INTO search_index (source_type, source_id, title)
  VALUES ('topic', NEW.id, NEW.title);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.description);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_topics_update
AFTER UPDATE ON discussion_topics
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'topic' AND source_id = OLD.id),
    OLD.title, OLD.description);
  UPDATE search_index
  SET title = NEW.title
  WHERE source_type = 'topic' AND source_id = NEW.id;
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (
    (SELECT id FROM search_index WHERE source_type = 'topic' AND source_id = NEW.id),
    NEW.title, NEW.description);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_topics_delete
AFTER DELETE ON discussion_topics
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'topic' AND source_id = OLD.id),
    OLD.title, OLD.description);
  DELETE FROM search_index WHERE source_type = 'topic' AND source_id = OLD.id;
END;

-- decisions

CREATE TRIGGER IF NOT EXISTS trg_search_decisions_insert
AFTER INSERT ON decisions
BEGIN
  INSERT INTO search_index (source_type, source_id, title)
  VALUES ('decision', NEW.id, NEW.decision);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.decision, NEW.reason);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_decisions_update
AFTER UPDATE ON decisions
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'decision' AND source_id = OLD.id),
    OLD.decision, OLD.reason);
  UPDATE search_index
  SET title = NEW.decision
  WHERE source_type = 'decision' AND source_id = NEW.id;
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (
    (SELECT id FROM search_index WHERE source_type = 'decision' AND source_id = NEW.id),
    NEW.decision, NEW.reason);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_decisions_delete
AFTER DELETE ON decisions
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'decision' AND source_id = OLD.id),
    OLD.decision, OLD.reason);
  DELETE FROM search_index WHERE source_type = 'decision' AND source_id = OLD.id;
END;

-- tasks

CREATE TRIGGER IF NOT EXISTS trg_search_tasks_insert
AFTER INSERT ON tasks
BEGIN
  INSERT INTO search_index (source_type, source_id, title)
  VALUES ('task', NEW.id, NEW.title);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.description);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_tasks_update
AFTER UPDATE ON tasks
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'task' AND source_id = OLD.id),
    OLD.title, OLD.description);
  UPDATE search_index
  SET title = NEW.title
  WHERE source_type = 'task' AND source_id = NEW.id;
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (
    (SELECT id FROM search_index WHERE source_type = 'task' AND source_id = NEW.id),
    NEW.title, NEW.description);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_tasks_delete
AFTER DELETE ON tasks
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'task' AND source_id = OLD.id),
    OLD.title, OLD.description);
  DELETE FROM search_index WHERE source_type = 'task' AND source_id = OLD.id;
END;

-- discussion_logs

CREATE TRIGGER IF NOT EXISTS trg_search_logs_insert
AFTER INSERT ON discussion_logs
BEGIN
  INSERT INTO search_index (source_type, source_id, title)
  VALUES ('log', NEW.id, NEW.title);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_logs_update
AFTER UPDATE ON discussion_logs
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'log' AND source_id = OLD.id),
    OLD.title, OLD.content);
  UPDATE search_index
  SET title = NEW.title
  WHERE source_type = 'log' AND source_id = NEW.id;
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (
    (SELECT id FROM search_index WHERE source_type = 'log' AND source_id = NEW.id),
    NEW.title, NEW.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_logs_delete
AFTER DELETE ON discussion_logs
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'log' AND source_id = OLD.id),
    OLD.title, OLD.content);
  DELETE FROM search_index WHERE source_type = 'log' AND source_id = OLD.id;
END;
