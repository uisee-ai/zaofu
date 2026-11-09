"""Export a UTF-8 JSON customer array as standard CSV."""

import argparse
import csv
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="UTF-8 JSON file containing customer records")
    args = parser.parse_args()
    try:
        with open(args.input, encoding="utf-8") as source:
            records = json.load(source)
        if not isinstance(records, list):
            raise ValueError("input must be a JSON array")
        for index, record in enumerate(records, 1):
            if not isinstance(record, dict):
                raise ValueError(f"record {index} must be an object")
            for field in ("name", "email"):
                if field not in record:
                    raise ValueError(f"record {index} is missing {field}")
                if not isinstance(record[field], str):
                    raise ValueError(f"record {index} field {field} must be a string")
    except json.JSONDecodeError as error:
        print(f"error: invalid JSON: {error}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    sys.stdout.reconfigure(encoding="utf-8", newline="")
    writer = csv.writer(sys.stdout)
    writer.writerow(("name", "email"))
    writer.writerows((record["name"], record["email"]) for record in records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
