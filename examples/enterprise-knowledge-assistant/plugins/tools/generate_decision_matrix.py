def generate_decision_matrix(input: dict) -> str:
    """Generate a structured comparison matrix between technologies using
    repository analysis and official documentation.

    Expects ``options`` (list of technology names) and optionally
    ``criteria`` (list of comparison dimensions) and ``scores`` (mapping of
    "option/criterion" to a short note). Returns a markdown table the
    comparison agent can present or refine.
    """
    options = [str(o) for o in input.get("options") or []]
    if not options:
        return "No options provided. Call with {'options': [...], 'criteria': [...]}."

    criteria = [str(c) for c in input.get("criteria") or []] or [
        "Maturity",
        "Ecosystem",
        "Learning curve",
        "Performance",
    ]
    scores: dict = input.get("scores") or {}

    header = "| Criterion | " + " | ".join(options) + " |"
    separator = "| --- " * (len(options) + 1) + "|"
    rows = [
        "| "
        + criterion
        + " | "
        + " | ".join(str(scores.get(f"{option}/{criterion}", "n/a")) for option in options)
        + " |"
        for criterion in criteria
    ]
    return "\n".join([header, separator, *rows])
