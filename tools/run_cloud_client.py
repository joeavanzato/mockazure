"""Child-only CA configuration for original Node Azure SDKs."""

import argparse
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ca", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[0] == "--" else args.command
    env = os.environ.copy()
    env["NODE_EXTRA_CA_CERTS"] = args.ca
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    raise SystemExit(subprocess.run(command, env=env).returncode)


if __name__ == "__main__":
    main()
