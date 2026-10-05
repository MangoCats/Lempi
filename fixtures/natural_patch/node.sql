-- A node that holds the same music as the baseline and numbers B and C the other way round (B is 3, C is 2),
-- with its own paths and its own first_seen.
INSERT INTO files VALUES (1, 'A', '/node/a', 'node-time'), (3, 'B', '/node/b', 'node-time'), (2, 'C', '/node/c', 'node-time');
INSERT INTO passages VALUES (10, 1, 'radio', 0, 1000, NULL), (11, 3, 'radio', 0, 500, NULL), (12, 2, 'radio', 0, 700, NULL);
INSERT INTO passage_recordings VALUES (10, 'm-a', 1.0), (11, 'm-b', 1.0), (12, 'm-c', 1.0);
