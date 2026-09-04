def echo(input: dict) -> str:
    """Echoes the input back. Useful for verifying the MCP wiring."""
    return str(input.get("text", input))