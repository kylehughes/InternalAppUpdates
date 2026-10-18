#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h}
source_directory="$repo_root/Sources/InternalAppUpdates"
fixture_directory="$repo_root/Tests/CompileFixtures"
temporary_directory=$(mktemp -d "${TMPDIR:-/tmp}/internal-app-updates-invariants.XXXXXX")
trap 'rm -rf "$temporary_directory"' EXIT

copied_sources="$temporary_directory/Sources"
mkdir -p "$copied_sources"
cp "$source_directory"/*.swift "$copied_sources"

source_files=("$copied_sources"/*.swift)
swift_architecture=$(uname -m)
swiftc_options=(-typecheck -parse-as-library -swift-version 6 -target "$swift_architecture-apple-macosx13.0")
passed_fixtures=0

record_pass() {
    passed_fixtures=$((passed_fixtures + 1))
    print -- "PASS ${1:t}"
}

run_positive_fixture() {
    local fixture=$1
    local controller_copy="$temporary_directory/UpdateController-positive.swift"
    cp "$copied_sources/UpdateController.swift" "$controller_copy"
    cat "$fixture" >> "$controller_copy"
    swiftc "${swiftc_options[@]}" "${source_files[@]:#${copied_sources}/UpdateController.swift}" "$controller_copy"
    record_pass "$fixture"
}

run_controller_failure_fixture() {
    local fixture=$1
    local expected_diagnostic=$2
    local controller_copy="$temporary_directory/UpdateController-negative.swift"
    local diagnostics="$temporary_directory/${fixture:t}.diagnostics"

    cp "$copied_sources/UpdateController.swift" "$controller_copy"
    cat "$fixture" >> "$controller_copy"
    if swiftc "${swiftc_options[@]}" "${source_files[@]:#${copied_sources}/UpdateController.swift}" "$controller_copy" >"$diagnostics" 2>&1; then
        print -u2 -- "Expected ${fixture:t} to fail compilation."
        return 1
    fi
    if ! grep -E -q -- "$expected_diagnostic" "$diagnostics"; then
        print -u2 -- "${fixture:t} failed without its expected diagnostic: $expected_diagnostic"
        cat "$diagnostics" >&2
        return 1
    fi
    record_pass "$fixture"
}

run_models_failure_fixture() {
    local fixture=$1
    local expected_diagnostic=$2
    local models_copy="$temporary_directory/Models-negative.swift"
    local diagnostics="$temporary_directory/${fixture:t}.diagnostics"

    cp "$copied_sources/Models.swift" "$models_copy"
    cat "$fixture" >> "$models_copy"
    if swiftc "${swiftc_options[@]}" "${source_files[@]:#${copied_sources}/Models.swift}" "$models_copy" >"$diagnostics" 2>&1; then
        print -u2 -- "Expected ${fixture:t} to fail compilation."
        return 1
    fi
    if ! grep -E -q -- "$expected_diagnostic" "$diagnostics"; then
        print -u2 -- "${fixture:t} failed without its expected diagnostic: $expected_diagnostic"
        cat "$diagnostics" >&2
        return 1
    fi
    record_pass "$fixture"
}

run_value_failure_fixture() {
    local fixture=$1
    local expected_diagnostic=$2
    local diagnostics="$temporary_directory/${fixture:t}.diagnostics"

    if swiftc "${swiftc_options[@]}" "$copied_sources"/*.swift "$fixture" >"$diagnostics" 2>&1; then
        print -u2 -- "Expected ${fixture:t} to fail compilation."
        return 1
    fi
    if ! grep -E -q -- "$expected_diagnostic" "$diagnostics"; then
        print -u2 -- "${fixture:t} failed without its expected diagnostic: $expected_diagnostic"
        cat "$diagnostics" >&2
        return 1
    fi
    record_pass "$fixture"
}

run_positive_fixture "$fixture_directory/positive-controller.swift"
run_controller_runtime_fixture() {
    local fixture=$1
    local controller_copy="$temporary_directory/UpdateController-runtime.swift"
    local executable="$temporary_directory/${fixture:t:r}"

    cp "$copied_sources/UpdateController.swift" "$controller_copy"
    cat "$fixture" >> "$controller_copy"
    swiftc -swift-version 6 -target "$swift_architecture-apple-macosx13.0" \
        "${source_files[@]:#${copied_sources}/UpdateController.swift}" "$controller_copy" -o "$executable"
    "$executable"
    record_pass "$fixture"
}

run_controller_runtime_fixture "$fixture_directory/runtime-controller-token-reference-identity.swift"
run_value_positive_fixture() {
    local fixture=$1
    swiftc "${swiftc_options[@]}" "$copied_sources"/*.swift "$fixture"
    record_pass "$fixture"
}

run_value_positive_fixture "$fixture_directory/positive-values.swift"

run_controller_failure_fixture "$fixture_directory/negative-controller-check-token-installation-token.swift" "cannot convert value of type 'UpdateController.InstallationToken' to expected argument type 'UpdateController.CheckToken'"
run_controller_failure_fixture "$fixture_directory/negative-controller-check-token-lifecycle-token.swift" "cannot convert value of type 'UpdateController.LifecycleToken' to expected argument type 'UpdateController.CheckToken'"
run_controller_failure_fixture "$fixture_directory/negative-controller-installation-token-lifecycle-token.swift" "cannot convert value of type 'UpdateController.LifecycleToken' to expected argument type 'UpdateController.InstallationToken'"
run_controller_failure_fixture "$fixture_directory/negative-controller-check-operation-missing-task.swift" "missing argument for parameter 'task' in call"
run_controller_failure_fixture "$fixture_directory/negative-controller-check-operation-missing-token.swift" "missing argument for parameter 'token' in call"
run_controller_failure_fixture "$fixture_directory/negative-controller-check-operation-missing-previous.swift" "missing argument for parameter 'previous' in call"
run_controller_failure_fixture "$fixture_directory/negative-controller-installation-operation-missing-release.swift" "missing argument for parameter 'release' in call"
run_controller_failure_fixture "$fixture_directory/negative-controller-installation-operation-missing-token.swift" "missing argument for parameter 'token' in call"
run_controller_failure_fixture "$fixture_directory/negative-controller-installation-operation-missing-mode.swift" "missing argument for parameter 'mode' in call"
run_controller_failure_fixture "$fixture_directory/negative-controller-running-used-as-settled.swift" "cannot convert value of type 'UpdateController.CheckOperation' to expected argument type 'UpdateController.CheckResult'"
run_controller_failure_fixture "$fixture_directory/negative-controller-check-operation-wrong-task.swift" "cannot convert value of type 'Task<Bool, Never>' to expected argument type 'Task<Void, Never>'"
run_controller_failure_fixture "$fixture_directory/negative-controller-installation-operation-wrong-token.swift" "cannot convert value of type 'UpdateController.CheckToken' to expected argument type 'UpdateController.InstallationToken'"
run_controller_failure_fixture "$fixture_directory/negative-controller-polling-operation-missing-task.swift" "missing argument for parameter 'task' in call"
run_controller_failure_fixture "$fixture_directory/negative-controller-lifecycle-active-without-operation.swift" "cannot convert value of type 'UpdateController.LifecycleToken' to expected argument type 'UpdateController.PollingOperation'"
run_controller_failure_fixture "$fixture_directory/negative-controller-lifecycle-inactive-without-token.swift" "missing argument for parameter #1 in call"

run_value_failure_fixture "$fixture_directory/negative-value-version-components-memberwise.swift" "no exact matches in call to initializer"
run_value_failure_fixture "$fixture_directory/negative-value-positive-duration-memberwise.swift" "extraneous argument label 'seconds:' in call"
run_value_failure_fixture "$fixture_directory/negative-value-positive-duration-known-valid.swift" "extraneous argument label 'knownValidSeconds:' in call"
run_value_failure_fixture "$fixture_directory/negative-value-installed-build-memberwise.swift" "extra arguments at positions #4, #5 in call"
run_value_failure_fixture "$fixture_directory/negative-value-update-release-memberwise.swift" "extra arguments at positions #9, #10, #11 in call"
run_value_failure_fixture "$fixture_directory/negative-value-update-configuration-memberwise.swift" "extra argument 'pollingInterval' in call"
run_models_failure_fixture "$fixture_directory/negative-value-version-components-mutation.swift" "cannot assign to property: 'major' is a 'let' constant"
run_value_failure_fixture "$fixture_directory/negative-value-positive-duration-mutation.swift" "cannot assign to property: 'seconds' is a 'let' constant"
run_value_failure_fixture "$fixture_directory/negative-value-installed-build-mutation.swift" "cannot assign to property: 'bundleIdentifier' is a 'let' constant"
run_value_failure_fixture "$fixture_directory/negative-value-update-release-mutation.swift" "cannot assign to property: 'id' is a 'let' constant"
run_value_failure_fixture "$fixture_directory/negative-value-update-configuration-mutation.swift" "cannot assign to property: 'feedURL' is a 'let' constant"

print -- "Compile invariant fixtures passed ($passed_fixtures fixtures)."

update_python="${INTERNAL_APP_UPDATES_PYTHON:-python3}"
if ! "$update_python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    print -u2 -- 'Set INTERNAL_APP_UPDATES_PYTHON to a Python 3.11 or newer interpreter.'
    exit 1
fi
PYTHONPATH="$repo_root/tools" "$update_python" -m unittest discover \
    -s "$repo_root/tools/tests" -p 'test_*invariants.py' -v
