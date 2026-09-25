-- Schema snapshot from delivered step 1.
BEGIN TRANSACTION;
CREATE TABLE collection_gaps (detail TEXT PRIMARY KEY);
CREATE TABLE collection_health (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), refreshed TEXT, error TEXT);
CREATE TABLE collection_links (
        task TEXT NOT NULL, bead TEXT NOT NULL, relation TEXT NOT NULL,
        PRIMARY KEY(task,bead,relation));
CREATE TABLE collection_tasks (
        task TEXT PRIMARY KEY, attempted TEXT, error TEXT, validated_path TEXT,
        host TEXT);
CREATE TABLE gaps (
        task TEXT NOT NULL, file TEXT NOT NULL DEFAULT '', device INTEGER NOT NULL,
        inode INTEGER NOT NULL, position INTEGER NOT NULL, detail TEXT NOT NULL,
        PRIMARY KEY(task, file, device, inode, position, detail));
CREATE TABLE response_estimates (
        response TEXT NOT NULL, tier TEXT NOT NULL, quote TEXT NOT NULL,
        PRIMARY KEY(response, tier));
CREATE TABLE responses (
        response TEXT PRIMARY KEY, task TEXT NOT NULL, turn TEXT, observed TEXT NOT NULL,
        usage TEXT, input INTEGER, cached INTEGER, cache_write INTEGER,
        output INTEGER, reasoning INTEGER,
        host TEXT NOT NULL DEFAULT 'codex', agent TEXT, model TEXT,
        modifier_key TEXT, modifiers TEXT, cache_write_1h INTEGER NOT NULL DEFAULT 0,
        complete INTEGER NOT NULL DEFAULT 1, skill TEXT);
CREATE TABLE sources (
        task TEXT NOT NULL, file TEXT NOT NULL DEFAULT '', path TEXT NOT NULL,
        device INTEGER NOT NULL, inode INTEGER NOT NULL,
        position INTEGER NOT NULL, skipping INTEGER NOT NULL,
        scanned TEXT NOT NULL, remaining INTEGER, incomplete INTEGER, error TEXT,
        PRIMARY KEY(task, file));
CREATE TABLE turn_models (
        task TEXT NOT NULL, turn TEXT NOT NULL, model TEXT NOT NULL,
        observed TEXT NOT NULL, conflicted INTEGER NOT NULL, PRIMARY KEY(task, turn));
CREATE INDEX responses_task ON responses(task);
COMMIT;
PRAGMA user_version=1;
