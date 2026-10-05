-- The hub's catalogue now. File A's first_seen moved (machine-scope: never sent). B's passage was
-- re-cut (0-500 to 0-450). File C was re-keyed (to C2). A's recording was removed; C's changed weight
-- (a child changing alone). D is new, with a passage and a recording.
INSERT INTO files VALUES (1, 'A', '/hub/a', 'later'), (2, 'B', '/hub/b', 'hub-time'), (3, 'C2', '/hub/c', 'hub-time'),
    (4, 'D', '/hub/d', 'hub-time');
INSERT INTO passages VALUES (10, 1, 'radio', 0, 1000, NULL), (11, 2, 'radio', 0, 450, NULL), (12, 3, 'radio', 0, 700, NULL),
    (13, 4, 'radio', 0, 900, NULL);
INSERT INTO passage_recordings VALUES (11, 'm-b', 1.0), (12, 'm-c', 0.5), (13, 'm-d', 1.0);
