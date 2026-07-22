#!/bin/sh
# Start the WSL computation worker with conservative authentication/bind policy.
# This script never installs or downloads PLIP, PyRosetta, or other dependencies.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

python_bin=${PYMOLPLUGINS_WORKER_PYTHON:-}
host=127.0.0.1
port=8765
max_body_mb=10
socket_timeout=15
allow_no_token=0
allow_non_loopback=0

usage() {
    cat <<'EOF'
Usage: scripts/start_worker.sh [options]

Start pymolplugins.worker.app from the repository root. This wrapper does not install
dependencies and never accepts a token as a command-line argument.

Options:
  --python PATH             Worker Python interpreter. Default order:
                            $PYMOLPLUGINS_WORKER_PYTHON,
                            .venv-worker/bin/python, python3.
  --host HOST               Bind address (default: 127.0.0.1).
  --port PORT               TCP port (default: 8765).
  --max-body-mb NUMBER      Request body limit in MiB (default: 10).
  --socket-timeout SECONDS  Client read timeout (default: 15).
  --allow-no-token          Explicitly allow no Bearer token on loopback only.
  --allow-non-loopback      Explicitly allow one exact non-loopback address;
                            requires a non-empty, sufficiently long token.
  -h, --help                Show this help.

Environment:
  PYMOLPLUGINS_WORKER_TOKEN   Bearer token; required by default.
  PYMOLPLUGINS_WORKER_PYTHON  Optional interpreter override.
  PYMOLPLUGINS_PYROSETTA_FLAGS Optional flags consumed by the backend.

Examples:
  export PYMOLPLUGINS_WORKER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  scripts/start_worker.sh
  scripts/start_worker.sh --python .venv-worker/bin/python --port 8876

For an exact WSL IPv4 fallback, use both --host <address> and
--allow-non-loopback. Wildcard binds (0.0.0.0 and ::) are always refused.
EOF
}

need_value() {
    if [ "$#" -lt 2 ]; then
        echo "Error: $1 requires a value" >&2
        usage >&2
        exit 2
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --python)
            need_value "$@"
            python_bin=$2
            shift 2
            ;;
        --host)
            need_value "$@"
            host=$2
            shift 2
            ;;
        --port)
            need_value "$@"
            port=$2
            shift 2
            ;;
        --max-body-mb)
            need_value "$@"
            max_body_mb=$2
            shift 2
            ;;
        --socket-timeout)
            need_value "$@"
            socket_timeout=$2
            shift 2
            ;;
        --allow-no-token)
            allow_no_token=1
            shift
            ;;
        --allow-non-loopback)
            allow_non_loopback=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$python_bin" ]; then
    if [ -x "$REPO_ROOT/.venv-worker/bin/python" ]; then
        python_bin=$REPO_ROOT/.venv-worker/bin/python
    else
        python_bin=python3
    fi
fi

case "$port" in
    ''|*[!0-9]*)
        echo "Error: --port must be an integer in [1, 65535]" >&2
        exit 2
        ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo "Error: --port must be an integer in [1, 65535]" >&2
    exit 2
fi

case "$host" in
    0.0.0.0|::|'[::]')
        echo "Error: wildcard binding is refused; use 127.0.0.1 or one exact WSL address" >&2
        exit 2
        ;;
    *:*)
        echo "Error: this conservative startup wrapper accepts IPv4 addresses only" >&2
        exit 2
        ;;
esac

is_loopback=0
case "$host" in
    127.0.0.1|localhost)
        is_loopback=1
        ;;
esac

token=${PYMOLPLUGINS_WORKER_TOKEN:-}
if [ -z "$token" ] && [ "$allow_no_token" -ne 1 ]; then
    echo "Error: PYMOLPLUGINS_WORKER_TOKEN is empty." >&2
    echo "Generate/export a token, or use --allow-no-token for an isolated loopback diagnostic." >&2
    exit 2
fi
if [ "$is_loopback" -ne 1 ]; then
    if [ "$allow_non_loopback" -ne 1 ]; then
        echo "Error: non-loopback host requires --allow-non-loopback" >&2
        exit 2
    fi
    if [ -z "$token" ]; then
        echo "Error: non-loopback binding requires PYMOLPLUGINS_WORKER_TOKEN" >&2
        exit 2
    fi
    if [ "${#token}" -lt 24 ]; then
        echo "Error: non-loopback token must contain at least 24 characters" >&2
        exit 2
    fi
elif [ -n "$token" ] && [ "${#token}" -lt 24 ]; then
    echo "Warning: worker token is short; generate a high-entropy token for routine use" >&2
fi

if ! command -v "$python_bin" >/dev/null 2>&1 && [ ! -x "$python_bin" ]; then
    echo "Error: worker Python is not executable: $python_bin" >&2
    exit 2
fi

cd "$REPO_ROOT"

if ! "$python_bin" -c 'import pymolplugins.worker.app' >/dev/null 2>&1; then
    echo "Error: selected Python cannot import pymolplugins.worker.app from $REPO_ROOT" >&2
    echo "Run: $python_bin scripts/probe_worker_env.py --pretty" >&2
    exit 1
fi

export PYTHONUNBUFFERED=1
echo "Starting PymolPlugins worker at http://$host:$port (auth_required=$([ -n "$token" ] && echo true || echo false))"
echo "Interpreter: $python_bin"
set -- "$python_bin" -m pymolplugins.worker.app \
    --host "$host" \
    --port "$port" \
    --max-body-mb "$max_body_mb" \
    --socket-timeout "$socket_timeout"
if [ "$is_loopback" -ne 1 ]; then
    set -- "$@" --allow-non-loopback
fi
exec "$@"
