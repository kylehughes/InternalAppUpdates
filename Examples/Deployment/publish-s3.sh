#!/bin/sh
# Copy this file and set these three values in your CI environment. Run one
# complete prepare/upload/verify workflow at a time for each app and channel.
# The local publisher lock does not serialize separate CI jobs or machines.
set -eu

: "${OUTPUT_DIRECTORY:?set OUTPUT_DIRECTORY to the generated site directory}"
: "${BASE_URL:?set BASE_URL to the public HTTPS URL}"
: "${S3_DESTINATION:?set S3_DESTINATION to s3://bucket/optional-prefix}"
PYTHON=${INTERNAL_APP_UPDATES_PYTHON:-python3}

# The selected interpreter must be the one where the package was installed.
"$PYTHON" -c 'import internal_app_updates'

if [ "$#" -ne 1 ]; then
    echo "usage: $0 /path/to/App.ipa" >&2
    exit 64
fi

# This workflow file must stay outside OUTPUT_DIRECTORY: it contains local
# paths and is not a website artifact. Override it for your build workspace.
REPORT_FILE=${PUBLICATION_REPORT:-"${OUTPUT_DIRECTORY%/}.publication.json"}
OUTPUT_DIRECTORY=$("$PYTHON" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$OUTPUT_DIRECTORY")
REPORT_FILE=$("$PYTHON" - "$OUTPUT_DIRECTORY" "$REPORT_FILE" <<'PY'
from pathlib import Path
import sys

output = Path(sys.argv[1])
report = Path(sys.argv[2]).resolve()
if report == output or report.is_relative_to(output):
    raise SystemExit("PUBLICATION_REPORT must be outside OUTPUT_DIRECTORY")
print(report)
PY
)

REPORT_PARENT=$(dirname "$REPORT_FILE")
mkdir -p "$REPORT_PARENT"
TEMP_REPORT=$(mktemp "$REPORT_PARENT/.internal-app-updates-report.XXXXXX")
cleanup() {
    rm -f "$TEMP_REPORT"
}
trap cleanup EXIT HUP INT TERM

if internal-app-updates publish \
    --ipa "$1" \
    --output "$OUTPUT_DIRECTORY" \
    --base-url "$BASE_URL" \
    --json > "$TEMP_REPORT"; then
    :
else
    status=$?
    cat "$TEMP_REPORT" >&2 || :
    exit "$status"
fi
mv "$TEMP_REPORT" "$REPORT_FILE"
trap - EXIT HUP INT TERM

SCRIPT_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$PYTHON" "$SCRIPT_DIRECTORY/upload-s3.py" "$REPORT_FILE" "$S3_DESTINATION"
