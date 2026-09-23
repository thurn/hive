"""Concise interactive output over the same values returned as JSON."""

from hive.jsonvalue import record, sequence, string


def task_line(value: object) -> str:
    task = record(value)
    state = record(task.get("state"))
    owner = state.get("owner")
    suffix = "" if owner is None else f" · owner {owner}"
    if "pause" in state:
        for raw in sequence(state["pause"], "pause conditions"):
            pause = record(raw)
            suffix += f" · {pause['reason']}: {pause['note']}"
    return f"[{task['id']}] P{task['priority']} {task['title']} · {state['status']}{suffix}"


def session_line(value: object) -> str:
    session = record(value)
    naming = record(session.get("naming"))
    name = f"{session['task']}: {session['title']} · title {naming['kind']}"
    if naming.get("kind") == "failed":
        name += f": {naming['detail']}"
    return name


def display(result: dict[str, object]) -> str:
    code = string(result.get("code"), "result code")
    if code == "CollectorStopped":
        return "Collector stopped."
    if code == "CollectionBatch":
        return (
            f"Attempted {result['attempted']} enrolled tasks.\n"
            f"Registry error: {result['registry_error']}; native index error: {result['native_index_error']}\n"
            f"Results: {result['results']}"
        )
    if code == "CollectorStatus":
        return (
            f"Enrolled: {result['enrolled']}; never attempted: {result['never_attempted']}\n"
            f"Registry refreshed: {result['registry_refreshed']}; error: {result['registry_error']}\n"
            f"Latest attempt: {result['latest_attempt']}; oldest attempt: {result['oldest_attempt']}\n"
            f"Recent failures: {result['recent_failures']}"
        )
    if code == "TranscriptCollected":
        return (
            f"{result['task']}: read {result['read_bytes']} bytes; "
            f"{result['remaining_bytes']} bytes remain; incomplete tail: {result['incomplete_tail']}"
            + (
                ""
                if result["error"] is None
                else f"\nSource unavailable: {result['error']}"
            )
        )
    if code == "ObservedUsage":
        known = result["known_tokens"]
        return (
            f"{result['task']}: {result['observed_responses']} observed responses; "
            f"{result['responses_missing_usage']} missing usage; {result['parse_gaps']} parse gaps\n"
            f"Known tokens: {'unknown' if known is None else known}\n"
            f"API-equivalent cost: unknown. {result['coverage']}\n"
            f"Last scan: {result['last_scan']}; remaining bytes: {result['remaining_bytes']}"
            + (
                ""
                if result["source_error"] is None
                else f"\nSource unavailable: {result['source_error']}"
            )
        )
    if code == "Session":
        result_session = record(result.get("session"))
        suffix = (
            "\nApply this title with the native naming tool; retry once on failure and record the outcome."
            if result_session.get("rename_required") is True
            else ""
        )
        return session_line(result_session) + suffix
    if code == "Sessions":
        return (
            "\n".join(
                session_line(s) for s in sequence(result.get("sessions"), "sessions")
            )
            or "No enrolled tasks."
        )
    if code == "SourceSelected":
        return f"Local master {result['commit']}\nSource: {result['directory']}"
    if code == "Configured":
        config = record(result.get("hive_config"))
        projects = [
            string(record(p).get("id"), "project")
            for p in sequence(config.get("projects"), "projects")
        ]
        lines = [
            f"Global capacity: {config['global_limit']}\nProjects: {', '.join(projects) or 'none'}"
        ]
        for raw in sequence(config.get("projects"), "projects"):
            project = record(raw)
            lines.append(
                f"{project['id']}: {project['repository']}\n  Invariants: {project['invariants']}\n  Native project: {project['native_id']}"
            )
        return "\n".join(lines)
    if code in {"Status", "Ready"}:
        lines: list[str] = []
        if code == "Status":
            owned = result.get("global_owned")
            limit = result.get("global_limit")
            lines.append(
                f"In flight: {'unknown' if owned is None else owned} / {'unknown' if limit is None else limit}"
            )
        else:
            lines.append("Ready work (claim required):")
        tasks = sequence(result.get("tasks"), "tasks")
        lines.extend(task_line(task) for task in tasks)
        if not tasks:
            lines.append("No matching work.")
        for problem in sequence(result.get("problems", []), "problems"):
            details = record(problem)
            lines.append(f"Invalid record {details['id']}: {details['detail']}")
        if code == "Status":
            lines.extend(
                session_line(s)
                for s in sequence(result.get("sessions", []), "sessions")
            )
            for problem in sequence(result.get("ui_problems", []), "UI problems"):
                details = record(problem)
                lines.append(f"Task UI unavailable: {details['detail']}")
            lines.append("Resource observations: unknown")
        return "\n".join(lines)
    if code == "Updated":
        return f"[{result['id']}] Priority set to P{result['priority']}"
    if code == "WorkspaceCreated":
        return f"[{result['bead']}] Workspace: {result['workspace']}\nBranch: {result['branch']}"
    if code in {"Submitted", "Authorized"}:
        return f"{code}: {result['candidate']}\nSource: {result['source']}"
    if code in {"Candidate", "Delivered"}:
        suffix = "" if result.get("reason") is None else f"\n{result['reason']}"
        return f"{result['candidate']}: {result['state']} · remote {result['remote']}{suffix}"
    if code == "Task":
        task = record(result.get("task"))
        state = record(task.get("state"))
        dependencies = [
            string(item, "dependency")
            for item in sequence(task.get("dependencies"), "dependencies")
        ]
        lines = [
            task_line(task),
            f"Project: {task['project']}",
            f"Description: {task['description']}",
            f"Acceptance: {task['acceptance']}",
            f"Dependencies: {', '.join(dependencies) or 'none'}",
        ]
        for section in ("phase", "outcome"):
            if section in state:
                details = record(state[section])
                lines.append(
                    f"{section.title()}: "
                    + ", ".join(f"{key}={value}" for key, value in details.items())
                )
        return "\n".join(lines)
    return f"{code}: {task_line(result.get('task'))}"
