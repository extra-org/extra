"""Fake bank data. Fixed values, no database, no network."""

from __future__ import annotations

# pydantic (which FastMCP uses to build tool schemas from these types) rejects
# typing.TypedDict on Python < 3.12, and the container runs 3.11.
from typing_extensions import TypedDict


class Account(TypedDict):
    account_id: str
    kind: str
    balance: float
    currency: str


class Transaction(TypedDict):
    date: str
    description: str
    amount: float


ACCOUNTS: list[Account] = [
    {"account_id": "•••4821", "kind": "checking", "balance": 2480.15, "currency": "USD"},
    {"account_id": "•••9034", "kind": "savings", "balance": 15200.00, "currency": "USD"},
]

TRANSACTIONS: list[Transaction] = [
    {"date": "2026-07-21", "description": "Rent", "amount": -1450.00},
    {"date": "2026-07-20", "description": "Salary", "amount": 3200.00},
    {"date": "2026-07-19", "description": "Groceries", "amount": -86.40},
    {"date": "2026-07-18", "description": "Coffee shop", "amount": -4.75},
    {"date": "2026-07-17", "description": "Electricity bill", "amount": -112.30},
]


def list_accounts() -> list[Account]:
    return ACCOUNTS


def recent_transactions(limit: int = 5) -> list[Transaction]:
    return TRANSACTIONS[: max(1, min(limit, len(TRANSACTIONS)))]
