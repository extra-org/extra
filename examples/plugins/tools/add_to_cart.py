"""Tool: add_to_cart

Add a product to the customer's supermarket cart by item name and quantity
"""
from __future__ import annotations

import random


def add_to_cart(item: str, quantity: int = 1) -> str:
    """Add a product to the customer's supermarket cart.

    Args:
        item: Name of the product to add (e.g. "milk", "bread").
        quantity: Number of units to add (default 1).

    Returns:
        Confirmation with updated cart total.
    """
    price_per_unit = round(random.uniform(1.5, 15.0), 2)
    total = round(price_per_unit * quantity, 2)
    return (
        f"🛒 Added to cart: {quantity}x {item} @ ${price_per_unit:.2f} each = ${total:.2f}\n"
        f"   Cart updated successfully."
    )
