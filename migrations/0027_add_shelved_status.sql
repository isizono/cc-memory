-- Migration 027: activitiesテーブルのCHECK制約にshelvedを追加
--
-- depends: 0026_add_snoozed_status
--
-- 背景:
--   「後で考える」系の話題を棚上げする「shelved」ステータスを追加する。
--   snoozedと異なり自動復活しない。期限未定で棚上げするユースケース向け。
--   SQLiteではCHECK制約のALTERができないため、テーブル再作成が必要。
--
-- 変更内容:
--   1. activitiesテーブルを再作成（CHECK制約にshelved追加）
--   2. データ移行
--   3. FTS5トリガー（activities関連3つ）をDROP→再CREATE

-- ================================================
-- Step 1: 新テーブル作成（shelved追加のCHECK制約）
-- ================================================

CREATE TABLE activities_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title VARCHAR(255) NOT NULL,
  description TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'in_progress', 'completed', 'snoozed', 'shelved')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_heartbeat_at TEXT
);

-- ================================================
-- Step 2: データ移行
-- ================================================

INSERT INTO activities_new SELECT
  id, title, description, status, created_at, updated_at, last_heartbeat_at
FROM activities;

-- ================================================
-- Step 3: FTS5トリガー一時削除（activitiesテーブルを参照しているもの）
-- ================================================

DROP TRIGGER IF EXISTS trg_search_activities_insert;
DROP TRIGGER IF EXISTS trg_search_activities_update;
DROP TRIGGER IF EXISTS trg_search_activities_delete;

-- ================================================
-- Step 4: 旧テーブル削除 + リネーム
-- ================================================

DROP TABLE activities;
ALTER TABLE activities_new RENAME TO activities;

-- ================================================
-- Step 5: インデックス再作成
-- ================================================

CREATE INDEX IF NOT EXISTS idx_activities_status ON activities(status);

-- ================================================
-- Step 6: FTS5トリガー再作成
-- 0011_rename_task_to_activity.sql で定義されたトリガーを再作成
-- ================================================

CREATE TRIGGER IF NOT EXISTS trg_search_activities_insert
AFTER INSERT ON activities
BEGIN
  INSERT INTO search_index (source_type, source_id, title)
  VALUES ('activity', NEW.id, NEW.title);
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (last_insert_rowid(), NEW.title, NEW.description);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_activities_update
AFTER UPDATE ON activities
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'activity' AND source_id = OLD.id),
    OLD.title, OLD.description);
  UPDATE search_index
  SET title = NEW.title
  WHERE source_type = 'activity' AND source_id = NEW.id;
  INSERT INTO search_index_fts (rowid, title, body)
  VALUES (
    (SELECT id FROM search_index WHERE source_type = 'activity' AND source_id = NEW.id),
    NEW.title, NEW.description);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_activities_delete
AFTER DELETE ON activities
BEGIN
  INSERT INTO search_index_fts (search_index_fts, rowid, title, body)
  VALUES ('delete',
    (SELECT id FROM search_index WHERE source_type = 'activity' AND source_id = OLD.id),
    OLD.title, OLD.description);
  DELETE FROM search_index WHERE source_type = 'activity' AND source_id = OLD.id;
END;
