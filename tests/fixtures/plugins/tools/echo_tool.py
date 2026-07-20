def echo_tool(input: dict) -> str:
    """Echo back the input text. Used to verify tool wiring end to end.
"""
    text = input.get("text", "")
    return f"echo: {text}"
