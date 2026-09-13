#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility entry point; the Tkinter shell has been retired."""

import sys


def main() -> None:
    # Select the isolated diagnostic before importing GUI or profile modules.
    if "--verify-backend" in sys.argv[1:]:
        if len(sys.argv) != 3 or sys.argv[1] != "--verify-backend":
            raise SystemExit(64)
        from backend_check import main as verify_backend

        raise SystemExit(verify_backend(sys.argv[2]))
    from app_shell import main as run_shell

    run_shell()


if __name__ == "__main__":
    main()
