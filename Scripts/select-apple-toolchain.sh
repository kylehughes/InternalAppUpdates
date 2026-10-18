#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  printf 'usage: %s <26.5|26.6>\n' "${0##*/}" >&2
  exit 64
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
toolchain="$(python3 - "$repo_root/Config/support.json" "$1" <<'PY'
import json
import sys

for candidate in json.load(open(sys.argv[1]))["appleToolchains"]:
    if candidate["xcode"] == sys.argv[2]:
        print("|".join([*candidate["developerDirectories"], candidate["xcode"], candidate["build"], candidate["swift"]]))
        break
else:
    raise SystemExit(64)
PY
)" || {
  printf 'unknown Apple toolchain: %s\n' "$1" >&2
  exit 64
}
IFS='|' read -r -a toolchain_parts <<< "$toolchain"
toolchain_part_count="${#toolchain_parts[@]}"
metadata_start=$((toolchain_part_count - 3))
expected_swift="${toolchain_parts[$((toolchain_part_count - 1))]}"
expected_build="${toolchain_parts[$((toolchain_part_count - 2))]}"
expected_xcode="${toolchain_parts[$((toolchain_part_count - 3))]}"
developer_directory=''
for candidate in "${toolchain_parts[@]:0:metadata_start}"; do
  if [[ -d "$candidate" ]]; then developer_directory="$candidate"; break; fi
done
if [[ -z "$developer_directory" ]]; then
  printf '::error::missing selected Xcode for lane %s\n' "$1" >&2
  exit 1
fi

actual_xcode="$(DEVELOPER_DIR="$developer_directory" xcodebuild -version)"
actual_swift="$(DEVELOPER_DIR="$developer_directory" swift --version 2>&1 | head -n 1)"
printf -v expected_xcode_output 'Xcode %s\nBuild version %s' "$expected_xcode" "$expected_build"
if [[ "$actual_xcode" != "$expected_xcode_output" ]]; then
  printf '::error::Xcode version mismatch: expected %q, got %q\n' "$expected_xcode_output" "$actual_xcode" >&2
  exit 1
fi
if [[ "$actual_swift" != *"Apple Swift version $expected_swift"* ]]; then
  printf '::error::Swift version mismatch: expected Apple Swift %s, got %s\n' "$expected_swift" "$actual_swift" >&2
  exit 1
fi

printf 'DEVELOPER_DIR=%s\n' "$developer_directory"
if [[ -n "${GITHUB_ENV:-}" ]]; then
  printf 'DEVELOPER_DIR=%s\n' "$developer_directory" >> "$GITHUB_ENV"
fi
