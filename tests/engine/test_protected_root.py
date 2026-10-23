"""Protected roots use the existing access policy before entering their executor."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_engine.approvals.errors import ApprovalInvocationMismatch, UnauthorizedApprover
from agent_engine.approvals.models import RunStatus
from agent_engine.core.spec import ResolverSpec
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.runtime.hooks import AuthContext, RunContext
from tests.approvals.test_engine_hitl import _multi_tool_spec
from tests.approvals.test_resume_binding import write_tool
from tests.engine.test_engine_flow import (
    agent,
    orchestrator,
    system,
    write_access,
    write_context_access,
    write_raising_access,
)
from tests.fixtures.utils import fake_model_factory


def _protected_root(kind):
    root = agent("admin", protected=True)
    if kind == "orchestrator":
        root = orchestrator("admin", [agent("public")])
        root = replace(root, node=replace(root.node, protected=True))
    return system(root)


@pytest.mark.parametrize("kind", ["agent", "orchestrator"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("context_kind", ["missing", "invalid", "allowed"])
async def test_root_policy_uses_request_context(tmp_path, kind, streaming, context_kind):
    write_context_access(tmp_path)
    context = None
    if context_kind != "missing":
        context = RunContext(
            metadata={"allowed_nodes": ("admin",) if context_kind == "allowed" else None},
            auth_context=AuthContext(
                inbound_access_token="test-only-secret", metadata={"groups": ("docs",)}
            ),
        )
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(_protected_root(kind))
        if streaming:
            events = [event async for event in engine.stream("hello", context=context)]
            result = events[-1]
            visited, answer = list(result.route or ()), result.content
        else:
            response = await engine.run("hello", context=context)
            visited, answer = response.visited, response.answer

    if context_kind == "allowed":
        assert visited == (["admin"] if kind == "agent" else ["admin", "admin/public"])
        assert answer == "ok"
    else:
        assert visited == []
        assert answer == "Access denied."


@pytest.mark.parametrize("kind", ["agent", "orchestrator"])
@pytest.mark.parametrize("exc", ["RuntimeError('private-policy-error')", "NotImplementedError"])
async def test_root_denied_when_access_resolver_raises(tmp_path: Path, kind, exc):
    write_raising_access(tmp_path, exc=exc)
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(_protected_root(kind))
        result = await engine.run("hello")

    assert result.visited == []
    assert result.answer == "Access denied."
    assert result.used_tools == ()


async def test_protected_root_requires_access_plugin_for_python_callers(tmp_path: Path):
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        with pytest.raises(FileNotFoundError, match=r"access\.py"):
            await engine.build(_protected_root("agent"))


@pytest.mark.parametrize("kind", ["agent", "orchestrator"])
async def test_denied_root_does_not_resolve_prompt_plugins(tmp_path: Path, kind):
    write_access(tmp_path, allow=False)
    accessed = tmp_path / "prompt-accessed"
    resolvers = tmp_path / "plugins" / "resolvers"
    resolvers.mkdir()
    (resolvers / "admin.py").write_text(
        "from pathlib import Path\n"
        "class Resolver:\n"
        "    def private_data(self, ctx):\n"
        f"        Path({str(accessed)!r}).write_text('accessed')\n"
        "        return 'private prompt data'\n",
        encoding="utf-8",
    )
    spec = _protected_root(kind)
    spec = replace(
        spec,
        graph=replace(
            spec.graph,
            node=replace(spec.graph.node, resolvers=(ResolverSpec("private_data", "agent"),)),
        ),
    )
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(spec)
        result = await engine.run("hello")

    assert not accessed.exists()
    assert result.visited == []
    assert result.answer == "Access denied."


@pytest.mark.parametrize("kind", ["agent", "orchestrator"])
async def test_root_access_decision_is_evaluated_for_each_request(tmp_path: Path, kind):
    write_context_access(tmp_path)
    permitted = RunContext(
        metadata={"allowed_nodes": ("admin",)},
        auth_context=AuthContext(metadata={"groups": ("docs",)}),
    )
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(_protected_root(kind))
        first = await engine.run("hello", context=permitted)
        denied = await engine.run("hello")
        last = await engine.run("hello", context=permitted)

    expected = ["admin"] if kind == "agent" else ["admin", "admin/public"]
    assert first.visited == last.visited == expected
    assert denied.visited == []
    assert denied.answer == "Access denied."


async def test_denial_reaches_the_streaming_chat_console(tmp_path: Path, capsys):
    from agentctl.chat import _answer_local_stream

    write_access(tmp_path, allow=False)
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(_protected_root("agent"))
        await _answer_local_stream(
            engine, "hello", context=None, read_line=lambda _: "", echo=print
        )

    assert "Access denied." in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["agent", "orchestrator"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("permitted", [False, True])
async def test_root_policy_is_rechecked_before_approval_replay(
    tmp_path: Path, kind, streaming, permitted
):
    allowed = tmp_path / "allowed"
    allowed.touch()
    write_access(tmp_path, allow=True)
    (tmp_path / "plugins" / "access.py").write_text(
        "from pathlib import Path\n"
        "class AccessResolver:\n"
        "    def can_access(self, ctx, node_id):\n"
        f"        return Path({str(allowed)!r}).exists()\n",
        encoding="utf-8",
    )
    output = tmp_path / "executed.log"
    write_tool(tmp_path, "send_email", output)
    spec = _multi_tool_spec("send_email")
    if kind == "orchestrator":
        spec = system(orchestrator("root", [spec.graph]))
    spec = replace(spec, graph=replace(spec.graph, node=replace(spec.graph.node, protected=True)))
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(spec)
        pending = await engine.run(
            "hello", context=RunContext(run_id="protected-run", user_id="user", conversation_id="s")
        )
        assert pending.pending_approval is not None
        assert not output.exists()
        approval_id = pending.pending_approval.approval_id
        if not permitted:
            allowed.unlink()

        async def resume():
            if streaming:
                return [
                    event
                    async for event in engine.resume_stream(
                        "protected-run",
                        approval_id,
                        "allow once",
                        caller_user_id="user",
                        caller_session_id="s",
                    )
                ]
            return await engine.resume(
                "protected-run",
                approval_id,
                "allow once",
                caller_user_id="user",
                caller_session_id="s",
            )

        if permitted:
            await resume()
            assert len(output.read_text().splitlines()) == 1
            assert await engine.get_run_status("protected-run") == RunStatus.COMPLETED
        else:
            # Root denial prevents reaching the reviewed tool. The existing
            # replay-binding guard rejects that decision rather than falsely
            # reporting that the approved invocation was executed.
            with pytest.raises(ApprovalInvocationMismatch):
                await resume()
            assert not output.exists()
            assert await engine.get_run_status("protected-run") == RunStatus.FAILED
            assert await engine.get_pending_approval("protected-run") is None
            assert (
                await engine.get_processed_result(
                    "protected-run", approval_id, caller_user_id="user", caller_session_id="s"
                )
                is None
            )


@pytest.mark.parametrize("streaming", [False, True])
async def test_approval_without_fresh_context_cannot_reuse_checkpoint_roles(tmp_path, streaming):
    output = tmp_path / "executed.log"
    write_tool(tmp_path, "send_email", output)
    (tmp_path / "plugins" / "access.py").write_text(
        "class AccessResolver:\n"
        "    def can_access(self, ctx, node_id):\n"
        "        return 'admin' in ctx.get('auth', {}).get('roles', ())\n",
        encoding="utf-8",
    )
    spec = _multi_tool_spec("send_email")
    spec = replace(spec, graph=replace(spec.graph, node=replace(spec.graph.node, protected=True)))
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(spec)
        pending = await engine.run(
            "hello", context=RunContext(run_id="r", auth_context=AuthContext(roles=("admin",)))
        )
        assert pending.pending_approval is not None
        with pytest.raises(ApprovalInvocationMismatch):
            if streaming:
                _ = [
                    event
                    async for event in engine.resume_stream(
                        "r", pending.pending_approval.approval_id, "allow once"
                    )
                ]
            else:
                await engine.resume("r", pending.pending_approval.approval_id, "allow once")
        assert not output.exists()


@pytest.mark.parametrize("fact", ["scopes", "metadata", "auth_metadata", "organization"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("permitted", [False, True])
async def test_approval_authorization_facts_are_fresh(tmp_path, fact, streaming, permitted):
    output = tmp_path / "executed.log"
    policy_calls = tmp_path / "policy.jsonl"
    write_tool(tmp_path, "send_email", output)
    conditions = {
        "scopes": "'write' in ctx.get('auth', {}).get('scopes', ())",
        "metadata": "ctx.get('metadata', {}).get('permission') == 'write'",
        "auth_metadata": "ctx.get('auth', {}).get('metadata', {}).get('permission') == 'write'",
        "organization": "ctx.get('auth', {}).get('organization_id') == 'org'",
    }
    (tmp_path / "plugins" / "access.py").write_text(
        "import json\n"
        "class AccessResolver:\n"
        "    def can_access(self, ctx, node_id):\n"
        f"        with open({str(policy_calls)!r}, 'a') as output:\n"
        "            output.write(json.dumps(ctx) + '\\n')\n"
        f"        return {conditions[fact]}\n",
        encoding="utf-8",
    )
    spec = _multi_tool_spec("send_email")
    spec = replace(spec, graph=replace(spec.graph, node=replace(spec.graph.node, protected=True)))
    initial = RunContext(
        run_id="r",
        user_id="user",
        conversation_id="s",
        metadata={"permission": "write"},
        auth_context=AuthContext(
            scopes=("write",),
            organization_id="org",
            metadata={"permission": "write"},
            inbound_access_token="old-secret",
        ),
    )
    # The resume host supplies a complete current snapshot; omitted facts must
    # not be filled from the original run, even when the checkpoint has them.
    current = initial.replace(
        metadata={"permission": "write"} if permitted else {},
        auth_context=AuthContext(
            scopes=("write",) if permitted else (),
            organization_id="org" if permitted else "other",
            metadata={"permission": "write"} if permitted else {},
            inbound_access_token="new-secret",
        ),
    )
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(spec)
        pending = await engine.run("hello", context=initial)
        assert pending.pending_approval is not None
        approval_id = pending.pending_approval.approval_id

        async def resume():
            if streaming:
                return [
                    event
                    async for event in engine.resume_stream(
                        "r", approval_id, "allow once", context=current
                    )
                ]
            return await engine.resume("r", approval_id, "allow once", context=current)

        if permitted:
            await resume()
            assert len(output.read_text().splitlines()) == 1
        else:
            with pytest.raises(ApprovalInvocationMismatch):
                await resume()
            assert not output.exists()
    calls = policy_calls.read_text()
    assert "old-secret" not in calls and "new-secret" not in calls
    latest = json.loads(calls.splitlines()[-1])
    assert latest["auth"].get("scopes", []) == (["write"] if permitted else [])
    assert latest["auth"]["organization_id"] == ("org" if permitted else "other")
    assert latest["metadata"].get("permission") == ("write" if permitted else None)


@pytest.mark.parametrize("identity", ["run", "session", "user", "auth_user"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_resume_context_cannot_replace_approval_ownership(tmp_path, identity, streaming):
    output = tmp_path / "executed.log"
    write_tool(tmp_path, "send_email", output)
    context = RunContext(run_id="r", user_id="owner", conversation_id="s")
    contexts = {
        "run": context.replace(run_id="another-run"),
        "session": context.replace(conversation_id="another-session"),
        "user": context.replace(user_id="another-user"),
        "auth_user": context.replace(auth_context=AuthContext(user_id="another-user")),
    }
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(_multi_tool_spec("send_email"))
        pending = await engine.run("hello", context=context)
        assert pending.pending_approval is not None
        with pytest.raises(UnauthorizedApprover):
            if streaming:
                _ = [
                    event
                    async for event in engine.resume_stream(
                        "r",
                        pending.pending_approval.approval_id,
                        "allow once",
                        caller_user_id="owner",
                        caller_session_id="s",
                        context=contexts[identity],
                    )
                ]
            else:
                await engine.resume(
                    "r",
                    pending.pending_approval.approval_id,
                    "allow once",
                    caller_user_id="owner",
                    caller_session_id="s",
                    context=contexts[identity],
                )
        assert not output.exists()
        assert await engine.get_run_status("r") == RunStatus.PENDING_APPROVAL
        assert await engine.get_pending_approval("r") is not None


@pytest.mark.parametrize("streaming", [False, True])
async def test_concurrent_approval_requests_do_not_share_authorization(tmp_path, streaming):
    output = tmp_path / "executed.log"
    write_tool(tmp_path, "send_email", output)
    (tmp_path / "plugins" / "access.py").write_text(
        "class AccessResolver:\n"
        "    def can_access(self, ctx, node_id):\n"
        "        return 'admin' in ctx.get('auth', {}).get('roles', ())\n",
        encoding="utf-8",
    )
    spec = _multi_tool_spec("send_email")
    spec = replace(spec, graph=replace(spec.graph, node=replace(spec.graph.node, protected=True)))
    async with LangGraphEngine(tmp_path, model_factory=fake_model_factory) as engine:
        await engine.build(spec)
        approvals: dict[str, str] = {}
        for run_id in ("allowed", "revoked"):
            pending = await engine.run(
                "hello",
                context=RunContext(run_id=run_id, auth_context=AuthContext(roles=("admin",))),
            )
            assert pending.pending_approval is not None
            approvals[run_id] = pending.pending_approval.approval_id

        async def resume_one(run_id):
            current = RunContext(
                auth_context=AuthContext(roles=("admin",) if run_id == "allowed" else ())
            )

            async def invoke():
                if streaming:
                    return [
                        event
                        async for event in engine.resume_stream(
                            run_id, approvals[run_id], "allow once", context=current
                        )
                    ]
                return await engine.resume(run_id, approvals[run_id], "allow once", context=current)

            if run_id == "allowed":
                await invoke()
            else:
                with pytest.raises(ApprovalInvocationMismatch):
                    await invoke()

        await asyncio.gather(resume_one("allowed"), resume_one("revoked"))
        assert len(output.read_text().splitlines()) == 1
        assert await engine.get_run_status("allowed") == RunStatus.COMPLETED
        assert await engine.get_run_status("revoked") == RunStatus.FAILED
