#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
update_python="${INTERNAL_APP_UPDATES_PYTHON:-python3}"
if ! "$update_python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo 'Set INTERNAL_APP_UPDATES_PYTHON to a Python 3.11 or newer interpreter.' >&2
  exit 1
fi
swift test
Scripts/check-invariants.sh
PYTHONPATH=tools "$update_python" -m unittest discover -s tools/tests -v
"$update_python" Examples/InternalAppUpdatesDemo/generate_project.py
xcodebuild -quiet \
  -project Examples/InternalAppUpdatesDemo/InternalAppUpdatesDemo.xcodeproj \
  -scheme InternalAppUpdatesDemo \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath .build/demo \
  CODE_SIGNING_ALLOWED=NO build
