#!/usr/bin/env bash

set -euo pipefail

configuration="${1:-Debug}"
case "$configuration" in Debug|Release) ;; *) printf 'usage: %s [Debug|Release]\n' "${0##*/}" >&2; exit 64;; esac
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python3 Examples/InternalAppUpdatesDemo/generate_project.py
project='Examples/InternalAppUpdatesDemo/InternalAppUpdatesDemo.xcodeproj'
derived_data='.build/ios-tests'
xcodebuild -project "$project" -scheme InternalAppUpdatesDemo -configuration "$configuration" -destination 'generic/platform=iOS Simulator' -derivedDataPath "$derived_data" CODE_SIGNING_ALLOWED=NO build
xcodebuild -project "$project" -scheme InternalAppUpdatesDemo -configuration "$configuration" -destination 'generic/platform=iOS' -derivedDataPath "$derived_data" CODE_SIGNING_ALLOWED=NO build
device_id="$(xcrun simctl list devices -j | python3 -c '
import json, re, sys
devices = json.load(sys.stdin)["devices"]
def key(runtime):
    return tuple(int(part) for part in re.findall(r"\d+", runtime))
available = [(runtime, device) for runtime, entries in devices.items() if ".iOS-" in runtime for device in entries if device.get("isAvailable") and "iPhone" in device["name"]]
if not available: raise SystemExit("no available iPhone simulator")
print(max(available, key=lambda item: key(item[0]))[1]["udid"])
')"
xcodebuild -scheme InternalAppUpdates -configuration "$configuration" -destination "id=$device_id" -derivedDataPath "$derived_data" test
