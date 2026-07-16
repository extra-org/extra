# Human-in-the-Loop approval demo

This runnable example proves the production `LangGraphEngine` approval flow end to end. It uses
the real YAML parser, graph runtime, approval coordinator, interrupt/resume provider, session
repository, execution ledger, and local-tool boundary. A deterministic model adapter replaces the
external LLM so the behavior is repeatable and requires no API key; it does not implement approval
logic.

## What is included

- `approval_demo_agent` and `second_approval_demo_agent` have `auto: false`.
- `auto_demo_agent` has `auto: true`.
- `write_demo_message` is harmless. It prints `[TOOL EXECUTED]`, increments a process-local counter,
  and returns a structured result.
- `RunContext.conversation_id` is where the demo passes the logical session ID.

The local tool follows the repository convention and lives at
`plugins/tools/write_demo_message.py`.

## Prerequisites

Install the project and development dependencies in the active Python environment. From the
repository root, use `python3` (not `python` on systems where only `python3` is installed).
No model API key is required for deterministic mode. Real-provider mode loads
`ANTHROPIC_API_KEY` from the first available `.env` in the example, `examples`, or repository root.
The selected agent uses `claude-haiku-4-5` through the production model factory. Routing remains
deterministic so an unrelated router choice cannot invalidate an approval scenario. The key is
never printed.

## Commands

Run these from `/Users/spwrwn/projects/extra`:

```bash
python3 examples/hitl-approval-demo/run_demo.py allow-once
python3 examples/hitl-approval-demo/run_demo.py allow-session
python3 examples/hitl-approval-demo/run_demo.py new-session
python3 examples/hitl-approval-demo/run_demo.py different-agent
python3 examples/hitl-approval-demo/run_demo.py deny
python3 examples/hitl-approval-demo/run_demo.py auto
python3 examples/hitl-approval-demo/run_demo.py all
```

To include real Anthropic API calls:

```bash
python3 examples/hitl-approval-demo/run_demo.py interactive --real-provider
python3 examples/hitl-approval-demo/run_demo.py allow-session --real-provider
```

For a real terminal choice, run:

```bash
python3 examples/hitl-approval-demo/run_demo.py interactive
```

Choose `1` for allow once, `2` for allow for this session, or `3` to deny. Free text is converted
once at this UI boundary into `ApprovalDecision`; the engine receives the typed decision.

## Expected scenarios

| Scenario | What it proves | Expected summary |
| --- | --- | --- |
| `allow-once` | Two calls both require approval | `approval_requests: 2`, `tool_executions: 2` |
| `allow-session` | The second same-session call skips approval | `approval_requests: 1`, `tool_executions: 2` |
| `new-session` | A grant does not cross session IDs | `approval_requests: 2`, `tool_executions: 2` |
| `different-agent` | A grant does not cross agent IDs | `approval_requests: 2`, `tool_executions: 2` |
| `deny` | Denial saves no permission and executes nothing | `approval_requests: 2`, `tool_executions: 0` |
| `auto` | `auto: true` bypasses the approval provider | `approval_requests: 0`, `tool_executions: 1` |

Before approval, output stops after `approval_required: true`; `[TOOL EXECUTED]` appears only after
the decision. With denial, that marker never appears.

For `ALLOW_FOR_SESSION`, the runner checks the repository through its public `is_allowed()` method.
It prints `Session approval saved` after the first call and `session_permission_found: True` before
the second call. The second call then prints `approval_prompt_skipped: true`.

## Approval and session semantics

- **Allow once** authorizes only the interrupted tool call. A later call prompts again.
- **Allow for this session** stores a grant keyed by system, user, session, agent, and stable tool
  identity. The local identity is provider-qualified, rather than only the short tool name.
- **New session** changes `RunContext.conversation_id`, so the prior grant does not match.
- **Different agent** changes the agent component of the key, so the prior grant does not match.
- **Deny** returns a controlled model-facing denial and never reaches the tool executor.
- **Auto mode** opens the central gate without consulting or writing the session repository.

The repository is in memory. Same-session verification must happen in the same Python process and
with the same injected repository instance. Approvals do not survive process restart and are not
shared across replicas.

## Output order

The important order is:

```text
engine_input: Use approval_demo_agent to write: interactive call
model_tool_request_observed: write_demo_message
approval_required: true
decision: allow_once
[TOOL EXECUTED] write_demo_message
```

The runner also prints safe identifiers and booleans: agent ID, session ID, local tool identity,
run ID, whether a permission was found, whether approval was required, the typed decision, and
whether execution was recorded. It does not print secrets.

## Troubleshooting

- `python: command not found`: use `python3` as shown above.
- Import errors: install the repository in the active environment and run from the repository root.
- Missing model credentials: this example intentionally needs none; verify you are running this
  `run_demo.py`, which injects the deterministic model adapter.
- Same-session call prompts again: run the complete `allow-session` scenario. Separate processes
  create separate in-memory repositories.
- Checkpointer warning: expected for this local demo. Both checkpoints and session grants are
  process-local.
