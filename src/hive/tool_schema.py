"""Small allocation facts; no prompt text or tool output is copied."""

import sqlite3


def create(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE allocation_seen (
        ordinal INTEGER PRIMARY KEY,task TEXT NOT NULL,file TEXT NOT NULL,
        identity TEXT NOT NULL,UNIQUE(task,file,identity))""")
    connection.execute("""CREATE TABLE allocation_cursor (
        task TEXT NOT NULL,agent TEXT NOT NULL,response TEXT,reset INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(task,agent))""")
    connection.execute("""CREATE TABLE allocation_responses (
        ordinal INTEGER PRIMARY KEY,response TEXT UNIQUE NOT NULL,task TEXT NOT NULL,
        agent TEXT NOT NULL,previous TEXT,reset INTEGER NOT NULL)""")
    connection.execute(
        "CREATE INDEX allocation_responses_task ON allocation_responses(task,ordinal)"
    )
    connection.execute(
        """CREATE TABLE pending_parts (
        ordinal INTEGER PRIMARY KEY,task TEXT NOT NULL,agent TEXT NOT NULL,record INTEGER NOT NULL,
        part_order INTEGER NOT NULL,kind TEXT NOT NULL,ref TEXT,name TEXT,bytes INTEGER NOT NULL)"""
    )
    connection.execute(
        "CREATE INDEX pending_parts_owner ON pending_parts(task,agent,ordinal)"
    )
    connection.execute("""CREATE TABLE segment_parts (
        task TEXT NOT NULL,agent TEXT NOT NULL,response TEXT NOT NULL,ordinal INTEGER NOT NULL,
        record INTEGER NOT NULL,part_order INTEGER NOT NULL,kind TEXT NOT NULL,ref TEXT,name TEXT,bytes INTEGER NOT NULL,
        PRIMARY KEY(response,ordinal))""")
    connection.execute(
        """CREATE TABLE response_blocks (
        response TEXT NOT NULL,block TEXT NOT NULL,ordinal INTEGER NOT NULL,record INTEGER NOT NULL,
        kind TEXT NOT NULL,name TEXT,ref TEXT,bytes INTEGER NOT NULL,output_count INTEGER NOT NULL,thinking_count INTEGER NOT NULL,PRIMARY KEY(response,block,ordinal))"""
    )
    connection.execute("""CREATE TABLE tool_oversized (
        task TEXT NOT NULL,file TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
        record INTEGER NOT NULL,PRIMARY KEY(task,file,start))""")
    connection.execute(
        "UPDATE sources SET position=0,skipping=0,remaining=NULL WHERE host='claude'"
    )
