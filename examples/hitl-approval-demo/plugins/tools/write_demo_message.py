from __future__ import annotations

import demo_state


def write_demo_message(message: str) -> dict[str, object]:
    """Print a visible marker and return a harmless structured result."""
    demo_state.execution_count += 1
    print("\n[TOOL EXECUTED] write_demo_message")
    print(f"message: {message}")
    print(f"execution_count: {demo_state.execution_count}")
    return {
        "status": "success",
        "message": message,
        "execution_count": demo_state.execution_count,
    }
