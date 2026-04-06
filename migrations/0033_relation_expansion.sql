-- Migration 033: リレーションテーブル統合 + decision_supersedes新設
--
-- depends: 0032_add_material_source
--
-- 背景:
--   旧スキーマでは5つの個別リレーションテーブル（topic_relations,
--   topic_activity_relations, activity_relations, topic_material_relations,
--   activity_material_relations）が存在し、エンティティタイプの組み合わせごとに
--   テーブルが分かれていた。decision/logへのリレーション拡張に伴い、
--   組み合わせ爆発を防ぐため統一テーブルに移行する。
--
-- 変更内容:
--   1. relations統一テーブルを新設（CHECK制約で正規化を強制）
--   2. decision_supersedesテーブルを新設（decision間の上書き関係）
--   3. 旧5テーブルからrelationsへデータ移行
--   4. 旧relations_viewを削除
--   5. 旧5テーブルを削除
--   6. CASCADE削除トリガー（ポリモーフィックFKのためトリガーで実現）
--   7. 新relations_viewを定義（4つのUNION ALL）

-- 1. relations統一テーブルを新設
CREATE TABLE relations (
    source_type TEXT NOT NULL CHECK(source_type IN ('topic', 'activity', 'material', 'decision', 'log')),
    source_id INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('topic', 'activity', 'material', 'decision', 'log')),
    target_id INTEGER NOT NULL,
    -- 現在はrelated専用。depends_onはactivity_dependencies、supersedesはdecision_supersedesで管理
    relation_type TEXT NOT NULL DEFAULT 'related' CHECK(relation_type = 'related'),
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (source_type, source_id, target_type, target_id),
    CHECK (source_type < target_type OR (source_type = target_type AND source_id < target_id))
);
-- PKは(source_type, source_id, ...)で始まるため、target側の検索用インデックスが必要
CREATE INDEX idx_relations_target ON relations(target_type, target_id);

-- 2. decision_supersedesテーブルを新設
CREATE TABLE decision_supersedes (
    source_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (source_id, target_id),
    CHECK (source_id != target_id)
);
CREATE INDEX idx_decision_supersedes_target ON decision_supersedes(target_id);

-- 3. 旧テーブルからデータ移行（正規化済み）

-- topic_relations: 同一type、id_1 < id_2 → そのまま
INSERT INTO relations (source_type, source_id, target_type, target_id, relation_type, created_at)
SELECT 'topic', topic_id_1, 'topic', topic_id_2, 'related', created_at
FROM topic_relations;

-- topic_activity_relations: 'activity' < 'topic' → source=activity, target=topic
INSERT INTO relations (source_type, source_id, target_type, target_id, relation_type, created_at)
SELECT 'activity', activity_id, 'topic', topic_id, 'related', created_at
FROM topic_activity_relations;

-- activity_relations: 同一type、id_1 < id_2 → そのまま
INSERT INTO relations (source_type, source_id, target_type, target_id, relation_type, created_at)
SELECT 'activity', activity_id_1, 'activity', activity_id_2, 'related', created_at
FROM activity_relations;

-- topic_material_relations: 'material' < 'topic' → source=material, target=topic
INSERT INTO relations (source_type, source_id, target_type, target_id, relation_type, created_at)
SELECT 'material', material_id, 'topic', topic_id, 'related', created_at
FROM topic_material_relations;

-- activity_material_relations: 'activity' < 'material' → source=activity, target=material
INSERT INTO relations (source_type, source_id, target_type, target_id, relation_type, created_at)
SELECT 'activity', activity_id, 'material', material_id, 'related', created_at
FROM activity_material_relations;

-- 4. 旧VIEW削除（旧テーブルを参照しているため、テーブルDROP前に必要）
DROP VIEW IF EXISTS relations_view;

-- 5. 旧テーブル削除
DROP TABLE IF EXISTS topic_relations;
DROP TABLE IF EXISTS topic_activity_relations;
DROP TABLE IF EXISTS activity_relations;
DROP TABLE IF EXISTS topic_material_relations;
DROP TABLE IF EXISTS activity_material_relations;

-- 6. CASCADE削除トリガー（relationsテーブルはポリモーフィックFKのため、トリガーで実現）
CREATE TRIGGER trg_relations_cascade_delete_topic
AFTER DELETE ON discussion_topics
FOR EACH ROW
BEGIN
    DELETE FROM relations WHERE (source_type = 'topic' AND source_id = OLD.id)
                             OR (target_type = 'topic' AND target_id = OLD.id);
END;

CREATE TRIGGER trg_relations_cascade_delete_activity
AFTER DELETE ON activities
FOR EACH ROW
BEGIN
    DELETE FROM relations WHERE (source_type = 'activity' AND source_id = OLD.id)
                             OR (target_type = 'activity' AND target_id = OLD.id);
END;

CREATE TRIGGER trg_relations_cascade_delete_material
AFTER DELETE ON materials
FOR EACH ROW
BEGIN
    DELETE FROM relations WHERE (source_type = 'material' AND source_id = OLD.id)
                             OR (target_type = 'material' AND target_id = OLD.id);
END;

CREATE TRIGGER trg_relations_cascade_delete_decision
AFTER DELETE ON decisions
FOR EACH ROW
BEGIN
    DELETE FROM relations WHERE (source_type = 'decision' AND source_id = OLD.id)
                             OR (target_type = 'decision' AND target_id = OLD.id);
END;

CREATE TRIGGER trg_relations_cascade_delete_log
AFTER DELETE ON discussion_logs
FOR EACH ROW
BEGIN
    DELETE FROM relations WHERE (source_type = 'log' AND source_id = OLD.id)
                             OR (target_type = 'log' AND target_id = OLD.id);
END;

-- 7. 新VIEW定義（relations + activity_dependencies + decision_supersedes）
CREATE VIEW relations_view AS
  -- related: 正方向
  SELECT source_type, source_id, target_type, target_id,
         'related' AS relation_type, created_at
  FROM relations
  UNION ALL
  -- related: 逆方向（対称リレーション）
  SELECT target_type, target_id, source_type, source_id,
         'related' AS relation_type, created_at
  FROM relations
  UNION ALL
  -- depends_on: 非対称（据え置き）
  SELECT 'activity' AS source_type, dependent_id AS source_id,
         'activity' AS target_type, dependency_id AS target_id,
         'depends_on' AS relation_type, created_at
  FROM activity_dependencies
  UNION ALL
  -- supersedes: 非対称（新規）
  SELECT 'decision' AS source_type, source_id,
         'decision' AS target_type, target_id,
         'supersedes' AS relation_type, created_at
  FROM decision_supersedes;
