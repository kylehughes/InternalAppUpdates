#!/usr/bin/env bash

set -euo pipefail

sanitizer="${1:-thread}"
case "$sanitizer" in thread|address) ;; *) printf 'usage: %s [thread|address]\n' "${0##*/}" >&2; exit 64;; esac
cd "$(dirname "$0")/.."
filter='(UpdateControllerTests|OperationInvariantTests|URLSessionUpdateFeedLoaderTests|UpdateControllerStateStreamTests)'
listed="$(swift test list | grep -E "$filter" || true)"
if [[ -z "$listed" ]]; then
  printf 'error: sanitizer filter selected no tests\n' >&2
  exit 1
fi
printf '%s\n' "$listed"
swift test --sanitize="$sanitizer" --filter "$filter"
