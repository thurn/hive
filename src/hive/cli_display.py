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


def display(result: dict[str, object]) -> str:
    code = string(result.get("code"), "result code")
    if code == "SourceSelected":
        return f"Local master {result['commit']}\nSource: {result['directory']}"
    if code == "Configured":
        config = record(result.get("hive_config"))
        projects = [
            string(record(p).get("id"), "project")
            for p in sequence(config.get("projects"), "projects")
        ]
        return f"Global capacity: {config['global_limit']}\nProjects: {', '.join(projects) or 'none'}"
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
