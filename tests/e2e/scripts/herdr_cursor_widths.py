"""Compare actual Herdr cursor reports with the Web Terminal Unicode addon."""
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import sys
import termios
import time
import tty

SAMPLES = ["a", "中", "⚠️", "👨‍👩‍👧", "👍🏽"]
SEQUENCES = [f"\x1b[3;1H{s}" for s in SAMPLES] + [
    "\x1b[5;118H中👍🏽a", "\x1b[5;118H中👍🏽a\bZ",
]


def measure_pane(output):
    with open("/dev/tty", "r+b", buffering=0) as handle:
        settings = termios.tcgetattr(handle)
        try:
            tty.setraw(handle)
            positions = []
            for sequence in SEQUENCES:
                handle.write(sequence.encode() + b"\x1b[6n")
                response = b""
                while not response.endswith(b"R"):
                    if not select.select([handle], [], [], 2)[0]:
                        raise RuntimeError("Herdr cursor report timed out")
                    response += handle.read(1)
                row, col = re.fullmatch(rb"\x1b\[(\d+);(\d+)R", response).groups()
                positions.append([int(row), int(col)])
            output.with_suffix(".next").write_text(json.dumps(positions))
            output.with_suffix(".next").replace(output)
        finally:
            termios.tcsetattr(handle, termios.TCSANOW, settings)


def verify(binary, root):
    # XDG paths are private to the runner, including server/session discovery.
    args = [binary, "--session", "zf-widths"]
    server = subprocess.Popen([*args, "server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        output = root / "cursor.json"
        for _ in range(50):
            result = subprocess.run([*args, "workspace", "list"], capture_output=True)
            if not result.returncode:
                break
            time.sleep(0.1)
        workspace = subprocess.check_output([*args, "workspace", "create", "--cwd", str(root)])
        pane = json.loads(workspace)["result"]["root_pane"]["pane_id"]
        command = shlex.join([sys.executable, str(Path(__file__).resolve()), "--pane", str(output)])
        subprocess.run([*args, "pane", "run", pane, command], check=True, capture_output=True)
        for _ in range(100):
            if output.exists():
                break
            time.sleep(0.1)
        host = json.loads(output.read_text())
        script = """
const { Terminal } = require('@xterm/xterm');
const { UnicodeGraphemesAddon } = require('@xterm/addon-unicode-graphemes');
(async () => {
  const term = new Terminal({ cols: 120, rows: 40, allowProposedApi: true });
  term.loadAddon(new UnicodeGraphemesAddon());
  const positions = [];
  for (const sequence of JSON.parse(process.argv[1])) {
    await new Promise(done => term.write(sequence, done));
    positions.push([term.buffer.active.cursorY + 1, term.buffer.active.cursorX + 1]);
  }
  console.log(JSON.stringify(positions));
  term.dispose();
})();
"""
        web = Path(__file__).resolve().parents[3] / "web"
        browser = json.loads(subprocess.check_output(["node", "-e", script, json.dumps(SEQUENCES)], cwd=web))
        if host != browser:
            raise AssertionError(f"Herdr {host} and xterm {browser} cursor positions differ")
        print(f"Herdr and xterm cursor positions (row, column): {host}", flush=True)
    finally:
        subprocess.run([*args, "server", "stop"], capture_output=True)
        server.terminate()
        server.wait(timeout=5)


if __name__ == "__main__":
    if sys.argv[1] == "--pane":
        measure_pane(Path(sys.argv[2]))
    else:
        verify(sys.argv[1], Path(os.environ["ZF_WEB_TERMINAL_FIXTURE"]))
