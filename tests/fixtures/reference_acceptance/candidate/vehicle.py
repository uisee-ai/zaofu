"""Vehicle records CLI for the local demonstration."""
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--role", choices=("admin", "reader"), required=True)
    parser.add_argument("action", choices=("detail", "timeline", "add", "edit", "delete"))
    parser.add_argument("vehicle")
    parser.add_argument("--record")
    parser.add_argument("--expires")
    args = parser.parse_args()

    if args.action == "detail":
        print(json.dumps({"vehicle": args.vehicle, "sections": ["warranty"]}))
        return 0
    print(json.dumps({"error": "section unavailable"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
