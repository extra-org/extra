#!/bin/sh
set -e

if [ "$1" = "serve" ] && [ -f /workspace/requirements.txt ]; then
    echo "Installing user dependencies..." >&2
    pip install -q -r /workspace/requirements.txt
fi

exec agentctl "$@"
