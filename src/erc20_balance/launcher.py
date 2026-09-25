"""Enter an isolated interpreter before loading network or UI code."""

import os
import sys


def main():
    os.environ.pop("SSLKEYLOGFILE", None)
    if not (sys.flags.isolated and sys.flags.dont_write_bytecode):
        os.execv(
            sys.executable,
            [sys.executable, "-I", "-B", "-m", "erc20_balance", *sys.argv[1:]],
        )
    from .cli import run

    return run()
