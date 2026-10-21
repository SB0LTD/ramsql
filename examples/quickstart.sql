CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, active BOOLEAN)
INSERT INTO projects VALUES (1, 'ramsql', TRUE)
INSERT INTO projects VALUES (2, 'sig', TRUE)
INSERT INTO projects VALUES (3, 'paused', FALSE)
SELECT id, name FROM projects WHERE active = TRUE ORDER BY id
UPDATE projects SET active = TRUE WHERE id = 3
DELETE FROM projects WHERE id = 2
SELECT * FROM projects ORDER BY id DESC LIMIT 2
