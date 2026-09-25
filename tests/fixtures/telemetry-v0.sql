-- The deployed pre-host schema, retained as an upgrade fixture.
CREATE TABLE sources (
 task TEXT PRIMARY KEY, path TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
 position INTEGER NOT NULL, skipping INTEGER NOT NULL, scanned TEXT NOT NULL,
 remaining INTEGER, incomplete INTEGER, error TEXT);
CREATE TABLE responses (
 response TEXT PRIMARY KEY, task TEXT NOT NULL, turn TEXT NOT NULL, observed TEXT NOT NULL,
 usage TEXT, input INTEGER, cached INTEGER, cache_write INTEGER, output INTEGER, reasoning INTEGER);
CREATE INDEX responses_task ON responses(task);
CREATE TABLE turn_models (
 task TEXT NOT NULL, turn TEXT NOT NULL, model TEXT NOT NULL, observed TEXT NOT NULL,
 conflicted INTEGER NOT NULL, PRIMARY KEY(task,turn));
CREATE TABLE response_estimates (
 response TEXT NOT NULL, tier TEXT NOT NULL, quote TEXT NOT NULL, PRIMARY KEY(response,tier));
CREATE TABLE gaps (
 task TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL, position INTEGER NOT NULL,
 detail TEXT NOT NULL, PRIMARY KEY(task,device,inode,position,detail));
CREATE TABLE collection_tasks (
 task TEXT PRIMARY KEY, attempted TEXT, error TEXT, validated_path TEXT);
CREATE TABLE collection_links (
 task TEXT NOT NULL, bead TEXT NOT NULL, relation TEXT NOT NULL, PRIMARY KEY(task,bead,relation));
CREATE TABLE collection_gaps (detail TEXT PRIMARY KEY);
CREATE TABLE collection_health (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), refreshed TEXT, error TEXT);
