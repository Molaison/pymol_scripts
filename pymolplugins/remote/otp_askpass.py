"""Standalone SSH_ASKPASS client. Its process receives no OTP seed."""

import json
import os
import socket
import sys


def main():
    try:
        request = {"token": os.environ["PYMOLPLUGINS_OTP_TOKEN"],
                   "prompt": sys.argv[1] if len(sys.argv) > 1 else ""}
        payload = json.dumps(request, ensure_ascii=True).encode("ascii") + b"\n"
        if len(payload) > 16384:
            return 1
        with socket.create_connection(("127.0.0.1", int(os.environ["PYMOLPLUGINS_OTP_PORT"])), timeout=5) as connection:
            connection.settimeout(300)
            connection.sendall(payload)
            with connection.makefile("rb") as stream:
                line = stream.readline(1025)
            if len(line) > 1024:
                return 1
            answer = json.loads(line).get("answer", "")
        if not isinstance(answer, str) or len(answer) != 6 or not answer.isascii() or not answer.isdigit():
            return 1
        sys.stdout.write(answer + "\n")
        sys.stdout.flush()
        return 0
    except (OSError, KeyError, ValueError, TypeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
