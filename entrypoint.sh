#!/bin/sh
set -e

if { [ "$1" = "serve" ] || [ "$1" = "agent-manager" ]; } && [ -f /workspace/requirements.txt ]; then
    echo "Installing user dependencies..." >&2
    pip install -q -r /workspace/requirements.txt
fi

# `agent-manager` is its own console script (the conversation/widget server),
# not an agentctl subcommand — dispatch to it directly.
if [ "$1" = "agent-manager" ]; then
    exec "$@"
fi

exec agentctl "$@"
