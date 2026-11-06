#!/bin/sh
# Check the bundled examples. The mixed file is unpinned on purpose,
# so its check exits 1 — that is the expected result, not a failure.
set -e
cd "$(dirname "$0")/.."
python3 src/requirements_pin_checker.py examples/pinned-requirements.txt
python3 src/requirements_pin_checker.py examples/mixed-requirements.txt || true
python3 src/requirements_pin_checker.py --json examples/mixed-requirements.txt > /dev/null || true
