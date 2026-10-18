#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 OUTPUT_DIRECTORY" >&2
    exit 64
fi

output_directory=$1
case "$output_directory" in
    /*) ;;
    *) output_directory="$(pwd)/$output_directory" ;;
esac

if [ -e "$output_directory" ] && [ "$(find "$output_directory" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "output directory must not exist or must be empty: $output_directory" >&2
    exit 1
fi

python=${INTERNAL_APP_UPDATES_PYTHON:-python3}
if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo 'Set INTERNAL_APP_UPDATES_PYTHON to a Python 3.11 or newer interpreter.' >&2
    exit 1
fi

repository_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
build_environment=$(mktemp -d "${TMPDIR:-/tmp}/internal-app-updates-build.XXXXXX")
validation_root=$(mktemp -d "${TMPDIR:-/tmp}/internal-app-updates-validation.XXXXXX")
trap 'rm -rf "$build_environment" "$validation_root"' EXIT HUP INT TERM

mkdir -p "$output_directory"
"$python" -m venv "$build_environment/builder"
"$build_environment/builder/bin/python" -m pip install --disable-pip-version-check --requirement "$repository_root/build-requirements.txt"
"$build_environment/builder/bin/python" -m build \
    --env-dir "$build_environment/isolated-build" \
    --sdist-extract-dir "$build_environment/sdist-extract" \
    --outdir "$output_directory" \
    "$repository_root"

wheel=$(find "$output_directory" -maxdepth 1 -type f -name 'internal_app_updates-*.whl' -print -quit)
sdist=$(find "$output_directory" -maxdepth 1 -type f -name 'internal_app_updates-*.tar.gz' -print -quit)
if [ -z "$wheel" ] || [ -z "$sdist" ] || [ "$(find "$output_directory" -maxdepth 1 -type f | wc -l | tr -d ' ')" -ne 2 ]; then
    echo 'expected exactly one wheel and one source distribution.' >&2
    exit 1
fi

expected_version=$("$python" - "$repository_root/pyproject.toml" <<'PY'
import pathlib
import sys
import tomllib

print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["project"]["version"])
PY
)

for artifact in "$wheel" "$sdist"; do
    environment=$(mktemp -d "$validation_root/install.XXXXXX")
    "$python" -m venv "$environment"
    "$environment/bin/python" -m pip install --disable-pip-version-check --requirement "$repository_root/build-requirements.txt"
    "$environment/bin/python" -m pip install --disable-pip-version-check --no-deps --no-build-isolation "$artifact"
    (
        cd "$environment"
        env -u PYTHONPATH "$environment/bin/internal-app-updates" --help >/dev/null
        env -u PYTHONPATH "$environment/bin/python" - "$expected_version" <<'PY'
import importlib.metadata
import sys

distribution = importlib.metadata.distribution("internal-app-updates")
if distribution.version != sys.argv[1]:
    raise SystemExit(f"expected version {sys.argv[1]}, found {distribution.version}")
for file in distribution.files or []:
    parts = file.parts
    if "tools" in parts or "tests" in parts:
        raise SystemExit(f"distribution unexpectedly contains {file}")
PY
    )
done

echo "built Python distributions in $output_directory"
