#!/bin/bash
# Create PMACS system users for process isolation (Architecture.md §3).
# Each PMACS process runs under its own user for defense-in-depth.
set -euo pipefail

USERS=(
    "_pmacs_inference"
    "_pmacs_cortex"
    "_pmacs_execution"
    "_pmacs_nervous"
    "_pmacs_stoploss"
    "_pmacs_mutation"
    "_pmacs_dashboard"
)

for user in "${USERS[@]}"; do
    if dscl . -read "/Users/${user}" UniqueID &>/dev/null; then
        echo "User ${user} already exists, skipping."
    else
        # Find next available UID (200+ range for service accounts)
        uid=$(dscl . -list /Users UniqueID | awk '{print $2}' | sort -n | tail -1)
        uid=$((uid + 1))
        if [ "$uid" -lt 200 ]; then
            uid=200
        fi

        dscl . -create "/Users/${user}"
        dscl . -create "/Users/${user}" UserShell /usr/bin/false
        dscl . -create "/Users/${user}" RealName "PMACS ${user#_pmacs_} process"
        dscl . -create "/Users/${user}" UniqueID "$uid"
        dscl . -create "/Users/${user}" PrimaryGroupID 20  # staff
        dscl . -create "/Users/${user}" NFSHomeDirectory /var/empty

        echo "Created user ${user} (UID=${uid})."
    fi
done

echo "Done. Verify: dscl . -list /Users | grep _pmacs"
