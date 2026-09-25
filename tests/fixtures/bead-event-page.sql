SELECT id, issue_id, event_type,
  CASE WHEN JSON_VALID(old_value) THEN JSON_UNQUOTE(JSON_EXTRACT(old_value,'$.status')) END AS old_status,
  CASE WHEN JSON_VALID(old_value) THEN JSON_UNQUOTE(JSON_EXTRACT(old_value,'$.assignee')) END AS old_assignee,
  CASE WHEN event_type <> 'closed' AND JSON_VALID(new_value)
       THEN JSON_UNQUOTE(JSON_EXTRACT(new_value,'$.status')) END AS new_status,
  CASE WHEN event_type <> 'closed' AND JSON_VALID(new_value)
        AND JSON_CONTAINS_PATH(new_value,'one','$.assignee')
       THEN CASE WHEN JSON_TYPE(JSON_EXTRACT(new_value,'$.assignee')) = 'NULL' THEN ''
                 ELSE JSON_UNQUOTE(JSON_EXTRACT(new_value,'$.assignee')) END END AS new_assignee,
  created_at, TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW()) AS server_offset
FROM events WHERE id > '00000000-0000-7000-8000-000000000000' ORDER BY id LIMIT 500
