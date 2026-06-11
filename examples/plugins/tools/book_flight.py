"""Tool: book_flight

Search and book a flight given origin, destination and travel date
"""
from __future__ import annotations

import random
import string


def book_flight(origin: str, destination: str, date: str) -> str:
    """Search and book a flight given origin, destination and travel date.

    Args:
        origin: Departure city or airport code (e.g. "Tel Aviv" or "TLV").
        destination: Arrival city or airport code (e.g. "London" or "LHR").
        date: Travel date in YYYY-MM-DD format.

    Returns:
        Booking confirmation with flight number and price.
    """
    confirmation = "FL-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    price = random.randint(150, 900)
    flight_number = "RL" + str(random.randint(100, 999))
    return (
        f"✈ Flight booked successfully!\n"
        f"  Flight   : {flight_number}\n"
        f"  Route    : {origin} → {destination}\n"
        f"  Date     : {date}\n"
        f"  Price    : ${price}\n"
        f"  Ref      : {confirmation}"
    )
