"""Process-local observable state for the harmless demo tool."""

execution_count = 0


def reset() -> None:
    global execution_count
    execution_count = 0
