"""Conversation lifecycle layer on top of the stateless agent engine.

`agent_manager` owns sessions, message history, and context windowing. It
depends on `agent_engine` (uses the `Engine` interface) and never the reverse,
keeping the engine a stateless brick.
"""
