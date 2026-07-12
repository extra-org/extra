def build_learning_plan(input: dict) -> str:
    """Build a personalized learning roadmap from the collected research
    findings.

    Expects ``topics`` (ordered list of things to learn) and optionally
    ``experience_level`` and ``weeks`` (total duration). Returns a phased
    markdown roadmap the learning planner presents to the user.
    """
    topics = [str(t) for t in input.get("topics") or []]
    if not topics:
        return "No topics provided. Call with {'topics': [...], 'weeks': 4}."

    level = str(input.get("experience_level") or "beginner")
    weeks = max(int(input.get("weeks") or len(topics)), 1)
    per_topic = max(weeks // len(topics), 1)

    lines = [f"# Learning plan ({level}, ~{weeks} weeks)", ""]
    week = 1
    for topic in topics:
        end = min(week + per_topic - 1, weeks)
        span = f"Week {week}" if end == week else f"Weeks {week}-{end}"
        lines.append(f"- **{span}:** {topic}")
        week = end + 1
    lines.append("")
    lines.append("Adjust pace as needed — each phase builds on the previous one.")
    return "\n".join(lines)
