-- The hub's catalogue as last sent: A, B, C numbered 1, 2, 3.
INSERT INTO files VALUES (1, 'A', '/hub/a', 'hub-time'), (2, 'B', '/hub/b', 'hub-time'), (3, 'C', '/hub/c', 'hub-time');
INSERT INTO passages VALUES (10, 1, 'radio', 0, 1000, NULL), (11, 2, 'radio', 0, 500, NULL), (12, 3, 'radio', 0, 700, NULL);
INSERT INTO passage_recordings VALUES (10, 'm-a', 1.0), (11, 'm-b', 1.0), (12, 'm-c', 1.0);
