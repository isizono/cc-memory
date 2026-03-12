-- depends: 0014_intent_namespace

-- NOTE: SQLiteのALTER TABLE ADD COLUMNではON DELETE SET NULLを指定できない。
-- canonical先タグの削除時は、事前にエイリアス解除（canonical_id = NULL）が必要。
-- tag_service.pyのupdate_tag()で逆引きチェックを行い、参照整合性を保証する。
ALTER TABLE tags ADD COLUMN canonical_id INTEGER REFERENCES tags(id);
