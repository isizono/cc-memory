-- depends: 0014_intent_namespace

ALTER TABLE tags ADD COLUMN canonical_id INTEGER REFERENCES tags(id);
