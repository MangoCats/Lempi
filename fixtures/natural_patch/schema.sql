CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT, first_seen TEXT);
CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, kind TEXT NOT NULL,
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, gain REAL);
CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL, weight REAL,
    PRIMARY KEY (passage_id, mbid));
