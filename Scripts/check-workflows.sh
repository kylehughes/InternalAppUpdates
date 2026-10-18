#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Pin the linter as well as the actions it checks. Downloads remain temporary.
version=1.7.12
if command -v actionlint >/dev/null 2>&1 && [[ "$(actionlint -version | head -n 1)" == "$version" ]]; then
    exec actionlint -shellcheck=
fi

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)
        platform=linux_amd64
        digest=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
        ;;
    Linux-aarch64)
        platform=linux_arm64
        digest=325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6
        ;;
    Darwin-arm64)
        platform=darwin_arm64
        digest=aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f
        ;;
    Darwin-x86_64)
        platform=darwin_amd64
        digest=5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644
        ;;
    *)
        printf 'Unsupported actionlint platform: %s %s\n' "$(uname -s)" "$(uname -m)" >&2
        exit 1
        ;;
esac

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
archive="actionlint_${version}_${platform}.tar.gz"
curl --fail --location --silent --show-error --connect-timeout 15 --max-time 120 \
    "https://github.com/rhysd/actionlint/releases/download/v${version}/${archive}" \
    --output "$temporary/$archive"
printf '%s  %s\n' "$digest" "$temporary/$archive" | shasum -a 256 --check
tar -xzf "$temporary/$archive" -C "$temporary" actionlint
"$temporary/actionlint" -shellcheck=
