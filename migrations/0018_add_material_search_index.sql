-- Migration 018: materialsをsearch_indexに登録し検索可能にする
--
-- depends: 0017_add_heartbeat
--
-- 変更内容:
--   - 既存materialレコードのsearch_index一括登録
--   - INSERT/UPDATE/DELETEトリガー作成

-- 1. 既存レコードのsearch_index一括登録
INSERT INTO search_index (source_type, source_id, title)
SELECT 'material', m.id, m.title
FROM materials m;

-- 対応するsearch_index_ftsへの登録
INSERT INTO search_index_fts (rowid, title, body)
SELECT si.id, si.title, m.content
FROM search_index si
INNER JOIN materials m ON si.source_id = m.id
WHERE si.source_type = 'material';

-- 2. トリガー

-- INSERT トリガー
CREATE TRIGGER IF NOT EXISTS trg_search_materials_insert
AFTER INSERT ON materials
BEGIN
  INSERT INTO search_index (source_type, source_id, title)
  VALUES ('material', NEW.id, NEW.title);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.content);
END;

-- UPDATE トリガー
CREATE TRIGGER IF NOT EXISTS trg_search_materials_update
AFTER UPDATE ON materials
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'material' AND source_id = OLD.id),
    OLD.title, OLD.content);
  UPDATE search_index
  SET title = NEW.title
  WHERE source_type = 'material' AND source_id = NEW.id;
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (
    (SELECT id FROM search_index WHERE source_type = 'material' AND source_id = NEW.id),
    NEW.title, NEW.content);
END;

-- DELETE トリガー
CREATE TRIGGER IF NOT EXISTS trg_search_materials_delete
AFTER DELETE ON materials
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'material' AND source_id = OLD.id),
    OLD.title, OLD.content);
  DELETE FROM search_index WHERE source_type = 'material' AND source_id = OLD.id;
END;
