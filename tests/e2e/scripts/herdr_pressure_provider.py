#!/usr/bin/env python3
"""Interactive local workload for the real-PTY browser test (no model calls)."""
import os
import select
import sys
import termios
import tty


def screen(tick: int) -> str:
    rows = [
        "".join(f"\x1b[38;2;{col};{row};{tick % 256}m{chr(33 + (tick + col + row) % 90)}"
                for col in range(110))
        for row in range(28)
    ]
    return "\x1b[H" + "\r\n".join(rows) + f"\x1b[0m\r\nPRESSURE {tick}\x1b[J"


def main() -> None:
    pid = os.getpid()
    print(f"PRESSURE READY pid={pid}", flush=True)
    for command in sys.stdin:
        if command.strip() == "ping":
            print(f"PONG pid={pid}", flush=True)
        elif command.strip() == "mobile":
            print(f"MOBILE pid={pid}", flush=True)
        elif command.strip() == "stream":
            original = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin)
            try:
                for tick in range(1500):
                    sys.stdout.write(screen(tick))
                    sys.stdout.flush()
                    if select.select([sys.stdin], [], [], 0.08)[0] and b"\x03" in os.read(0, 64):
                        print(f"\x1b[0m\x1b[H\x1b[2JINTERRUPTED pid={pid}", flush=True)
                        break
            finally:
                termios.tcsetattr(sys.stdin, termios.TCSANOW, original)


if __name__ == "__main__":
    main()
