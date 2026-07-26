def block_card(input: dict) -> str:
    """Block a card and order a replacement.

    Local tools take one dict and return a string. The dict is whatever the
    model decided to pass, so treat every key as untrusted and missing.
    """
    last_four = str(input.get("last_four") or "").strip()
    if not last_four.isdigit() or len(last_four) != 4:
        return "Need the last four digits of the card, e.g. {'last_four': '4821'}."

    # A real implementation would call the card system here.
    return (
        f"Card ending {last_four} is blocked. A replacement is on its way and "
        f"should arrive within 5 working days."
    )
