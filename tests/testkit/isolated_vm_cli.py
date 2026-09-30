"""VM-only CLI observation wrapper; preserve argv, exit status and time budget."""

from __future__ import annotations

import faulthandler
import runpy


def main() -> None:
    # stderr stays captured by the admission caller. Never exit from the timer
    # or alter CLI behavior: the existing subprocess timeout remains authority.
    faulthandler.dump_traceback_later(10, repeat=True, exit=False)
    try:
        runpy.run_module("zf.cli.main", run_name="__main__", alter_sys=True)
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
