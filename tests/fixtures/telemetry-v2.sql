BEGIN TRANSACTION;
CREATE TABLE claude_agents (
        task TEXT NOT NULL, agent TEXT NOT NULL, tool_use_id TEXT, agent_type TEXT,
        description TEXT, spawn_depth INTEGER, PRIMARY KEY(task,agent));
CREATE TABLE claude_response_counts (
        response TEXT NOT NULL, output INTEGER NOT NULL, reasoning INTEGER NOT NULL,
        PRIMARY KEY(response,output,reasoning));
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
        complete INTEGER NOT NULL DEFAULT 1, skill TEXT, flags TEXT NOT NULL DEFAULT '[]');
CREATE TABLE sources (
        task TEXT NOT NULL, file TEXT NOT NULL DEFAULT '', path TEXT NOT NULL,
        device INTEGER NOT NULL, inode INTEGER NOT NULL,
        position INTEGER NOT NULL, skipping INTEGER NOT NULL,
        scanned TEXT NOT NULL, remaining INTEGER, incomplete INTEGER, error TEXT, host TEXT NOT NULL DEFAULT 'codex',
        PRIMARY KEY(task, file));
CREATE TABLE turn_models (
        task TEXT NOT NULL, turn TEXT NOT NULL, model TEXT NOT NULL,
        observed TEXT NOT NULL, conflicted INTEGER NOT NULL, PRIMARY KEY(task, turn));
CREATE INDEX responses_task ON responses(task);
COMMIT;
