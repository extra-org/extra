"""Deterministic mock enterprise data used by the local MCP server."""

from __future__ import annotations

import hashlib

# typing_extensions rather than typing: pydantic (which FastMCP uses to build
# tool schemas from these types) rejects typing.TypedDict on Python < 3.12,
# and this project supports 3.11.
from typing_extensions import TypedDict


class Document(TypedDict):
    document_id: str
    title: str
    summary: str


class Employee(TypedDict):
    employee_id: str
    name: str
    team: str
    role: str


class PublishedNote(TypedDict):
    note_id: str
    title: str
    status: str


_DOCUMENTS: tuple[Document, ...] = (
    {
        "document_id": "DOC-001",
        "title": "Session Approval Policy",
        "summary": "Approvals are scoped to one session and one fully qualified tool identity.",
    },
    {
        "document_id": "DOC-002",
        "title": "Internal Knowledge Search",
        "summary": "Enterprise search returns deterministic mock documents in the local demo.",
    },
    {
        "document_id": "DOC-003",
        "title": "Publishing Guidelines",
        "summary": "Internal notes require a separate approval from read-only knowledge tools.",
    },
)

_EMPLOYEES: dict[str, Employee] = {
    "E-100": {
        "employee_id": "E-100",
        "name": "Ari Cohen",
        "team": "Knowledge Platform",
        "role": "Staff Engineer",
    },
    "E-200": {
        "employee_id": "E-200",
        "name": "Maya Levi",
        "team": "Developer Experience",
        "role": "Engineering Manager",
    },
}


def search_internal_documents(query: str) -> list[Document]:
    """Search deterministic mock documents by title and summary."""
    normalized = query.casefold().strip()
    if not normalized:
        return list(_DOCUMENTS)
    return [
        document
        for document in _DOCUMENTS
        if normalized in f"{document['title']} {document['summary']}".casefold()
    ]


def get_employee_information(employee_id: str) -> Employee | dict[str, str]:
    """Return deterministic employee data for a known mock identifier."""
    employee = _EMPLOYEES.get(employee_id.upper())
    if employee is not None:
        return employee
    return {"employee_id": employee_id, "status": "not_found"}


def publish_internal_note(title: str, content: str) -> PublishedNote:
    """Simulate publishing without storing data or performing side effects."""
    digest = hashlib.sha256(f"{title}\0{content}".encode()).hexdigest()[:10]
    return {
        "note_id": f"NOTE-{digest}",
        "title": title,
        "status": "published_demo_only",
    }
