-- Migration 008: discussion_logsをsearch_indexに登録し検索可能にする
--
-- depends: 0007_remove_blocked_status
--
-- 変更内容:
--   - discussion_logsにtitleカラムを追加
--   - 既存レコードのsearch_index一括登録
--   - INSERT/UPDATE/DELETEトリガー作成

-- 1. titleカラム追加
ALTER TABLE discussion_logs ADD COLUMN title TEXT NOT NULL DEFAULT '';

-- 2. 既存レコードのsearch_index一括登録
INSERT INTO search_index (source_type, source_id, subject_id, title)
SELECT 'log', dl.id,
  (SELECT subject_id FROM discussion_topics WHERE id = dl.topic_id),
  dl.title
FROM discussion_logs dl;

-- 対応するsearch_index_ftsへの登録
INSERT INTO search_index_fts (rowid, title, body)
SELECT si.id, si.title, dl.content
FROM search_index si
INNER JOIN discussion_logs dl ON si.source_id = dl.id
WHERE si.source_type = 'log';

-- 3. トリガー

-- INSERT トリガー
CREATE TRIGGER IF NOT EXISTS trg_search_logs_insert
AFTER INSERT ON discussion_logs
BEGIN
  INSERT INTO search_index (source_type, source_id, subject_id, title)
  VALUES ('log', NEW.id,
    (SELECT subject_id FROM discussion_topics WHERE id = NEW.topic_id),
    NEW.title);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.content);
END;

-- UPDATE トリガー
CREATE TRIGGER IF NOT EXISTS trg_search_logs_update
AFTER UPDATE ON discussion_logs
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'log' AND source_id = OLD.id),
    OLD.title, OLD.content);
  UPDATE search_index
  SET title = NEW.title,
      subject_id = COALESCE(
        (SELECT subject_id FROM discussion_topics WHERE id = NEW.topic_id),
        subject_id)
  WHERE source_type = 'log' AND source_id = NEW.id;
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (
    (SELECT id FROM search_index WHERE source_type = 'log' AND source_id = NEW.id),
    NEW.title, NEW.content);
END;

-- DELETE トリガー
CREATE TRIGGER IF NOT EXISTS trg_search_logs_delete
AFTER DELETE ON discussion_logs
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'log' AND source_id = OLD.id),
    OLD.title, OLD.content);
  DELETE FROM search_index WHERE source_type = 'log' AND source_id = OLD.id;
END;
