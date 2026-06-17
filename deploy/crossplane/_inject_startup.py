#!/usr/bin/env python3
"""Splice deploy/startup.sh into the Instance MR's metadataStartupScript field.

Reads rendered kustomize YAML on stdin, writes the spliced YAML to stdout.
No third-party deps — string replace of the single empty `metadataStartupScript: ""`
with a YAML literal block holding the script. Path to the script comes from the
STARTUP_PATH env var.
"""

import os
import sys


def main() -> int:
    docs = sys.stdin.read()
    with open(os.environ["STARTUP_PATH"]) as f:
        script = f.read()

    # Indent each script line for a YAML literal block. The field is at 4-space
    # indent; literal block content goes one level deeper (6 spaces).
    indent = "      "
    block = "\n".join((indent + line) if line else "" for line in script.splitlines())

    needle = '    metadataStartupScript: ""'
    replacement = "    metadataStartupScript: |\n" + block

    if needle not in docs:
        sys.stderr.write(
            "ERROR: did not find empty metadataStartupScript to inject into.\n"
        )
        return 1

    # Only the Instance has this field; replace the single occurrence.
    sys.stdout.write(docs.replace(needle, replacement, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
