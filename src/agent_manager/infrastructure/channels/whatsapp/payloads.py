"""Strict boundary models. Unknown provider fields never become domain context."""

import builtins
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=512)]
Timestamp = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]


class Payload(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)


class Envelope(Payload):
    object: Identifier
    entry: list[builtins.object] = Field(default_factory=list, max_length=100)


class Entry(Payload):
    id: Identifier
    changes: list[object] = Field(max_length=100)


class Change(Payload):
    field: Identifier
    value: object


class Metadata(Payload):
    phone_number_id: Identifier


class RoutingValue(Payload):
    messaging_product: Literal["whatsapp"]
    metadata: Metadata


class Messages(Payload):
    messages: list[object] = Field(default_factory=list, max_length=1000)


class Statuses(Payload):
    statuses: list[object] = Field(default_factory=list, max_length=1000)


class Text(Payload):
    body: Annotated[str, StringConstraints(min_length=1, max_length=65_536)]


class Message(Payload):
    id: Identifier
    sender: Identifier = Field(alias="from")
    timestamp: Timestamp
    type: Identifier
    text: Text | None = None


class Status(Payload):
    id: Identifier
    recipient_id: Identifier
    timestamp: Timestamp
    status: Annotated[str, StringConstraints(min_length=1, max_length=64)]
