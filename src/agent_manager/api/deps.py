"""FastAPI dependencies. The composition root puts collaborators on app.state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from agent_manager.application import ConversationService
from agent_manager.domain import Principal
from agent_manager.infrastructure.auth import IdentityResolver, TokenError

logger = logging.getLogger(__name__)

BEARER_PREFIX = "Bearer "
UNAUTHENTICATED_DETAIL = "a verified identity is required"
VISITOR_PASS_HEADER = "X-Extra-Visitor-Pass"


@dataclass(frozen=True)
class CallerIdentity:
    """Everything the HTTP edge needs to name a caller: where a token may be
    found, and who can verify it."""

    resolver: IdentityResolver
    cookie_name: str | None = None


def get_service(request: Request) -> ConversationService:
    return request.app.state.service


def get_caller_identity(request: Request) -> CallerIdentity:
    return request.app.state.caller_identity


async def get_principal(request: Request) -> Principal:
    """The proven caller every conversation route authorizes against.

    A bearer token where the caller supplied one, otherwise the host's session
    cookie. Trusting that cookie is safe because it only reaches us from the
    host's own origin: cross-site requests cannot read a JSON response, and the
    widget's `application/json` writes are preflighted against a CORS allowlist
    that denies by default.

    After resolving an authenticated (non-anonymous) principal, the dependency
    opportunistically adopts any anonymous history the widget attached via the
    X-Extra-Visitor-Pass header. Failures are logged and silently swallowed so
    an adoption hiccup never blocks the actual request.
    """
    identity = get_caller_identity(request)
    token = _select_token(request, identity)
    if token is None:
        raise HTTPException(status_code=401, detail=UNAUTHENTICATED_DETAIL)
    try:
        principal = identity.resolver.resolve(token)
    except TokenError as exc:
        logger.warning("token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from None

    # Server-side opportunistic hand-off.
    # When both an authenticated principal AND a visitor pass arrive on the same
    # request, adopt the anonymous history before the route runs. This removes
    # the need for the frontend to repeatedly probe /auth/link in cookie mode.
    if not principal.is_anonymous:
        raw_pass = request.headers.get(VISITOR_PASS_HEADER)
        if raw_pass:
            await _try_adopt_visitor_history(request, raw_pass, principal, identity)

    return principal


async def _try_adopt_visitor_history(
    request: Request,
    raw_pass: str,
    principal: Principal,
    identity: CallerIdentity,
) -> None:
    """Opportunistically adopt anonymous history. Failures never block the request.

    Invalid/expired pass: TokenError is caught and logged at DEBUG — the
    authenticated request continues normally and the stale pass is effectively
    discarded.

    Temporary DB failure: Exception is caught and logged at WARNING — the pass
    is NOT marked consumed so the next request will retry automatically.

    Already-adopted pass: link_anonymous_user runs an atomic UPDATE WHERE
    linked_to_user_id IS NULL, which affects 0 rows and returns cleanly.
    """
    try:
        visitor = identity.resolver.anonymous.resolve(raw_pass)
    except TokenError:
        logger.debug("X-Extra-Visitor-Pass is invalid or expired; ignoring")
        return
    try:
        service = get_service(request)
        await service.link_anonymous(visitor, principal)
    except Exception:
        logger.warning("opportunistic anonymous history adoption failed", exc_info=True)


Service = Annotated[ConversationService, Depends(get_service)]
Caller = Annotated[Principal, Depends(get_principal)]
Identity = Annotated[CallerIdentity, Depends(get_caller_identity)]


def _select_token(request: Request, identity: CallerIdentity) -> str | None:
    """Which of the two places a token can arrive in names the caller.

    A bearer token naming a host user is something the caller sent on purpose,
    so it wins — asking to run as someone is not overridden by whoever this
    browser happens to be logged in as. A visitor pass is not deliberate: the
    widget keeps it from before the user signed in, so letting it outrank the
    session cookie would hold that user anonymous for as long as the pass
    lived — across reloads, because nothing on the client knows the cookie
    appeared.
    """
    bearer = _bearer_token(request)
    cookie = _cookie_token(request, identity.cookie_name)
    if bearer is None or (cookie is not None and not identity.resolver.names_a_host_user(bearer)):
        return cookie
    return bearer


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith(BEARER_PREFIX):
        return None
    return header.removeprefix(BEARER_PREFIX).strip() or None


def _cookie_token(request: Request, cookie_name: str | None) -> str | None:
    return request.cookies.get(cookie_name) if cookie_name else None
