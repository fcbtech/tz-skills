#!/usr/bin/env bash
# uninstall-sessions-digest.sh — remove the sessions-digest launchd job.
#
# Unloads the launchd job and removes the plist. Leaves the script in ~/bin/
# and any logs in ~/Library/Logs/ alone (delete those yourself if you want).
#
# Usage:
#   bin/uninstall-sessions-digest.sh
#   bin/uninstall-sessions-digest.sh --dry-run

set -euo pipefail

PLIST_DST="${HOME}/Library/LaunchAgents/com.tranzact.sessions-digest.plist"
LABEL="com.tranzact.sessions-digest"

DRY=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '1,12p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 1 ;;
    esac
done

if [[ ! -f $PLIST_DST ]] && ! launchctl list "$LABEL" >/dev/null 2>&1; then
    echo "nothing to do: $LABEL not loaded, $PLIST_DST not present"
    exit 0
fi

if [[ $DRY -eq 1 ]]; then
    echo "Would unload $LABEL and remove $PLIST_DST"
    exit 0
fi

if launchctl list "$LABEL" >/dev/null 2>&1; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    echo "ok: unloaded $LABEL"
fi

if [[ -f $PLIST_DST ]]; then
    rm -f "$PLIST_DST"
    echo "ok: removed $PLIST_DST"
fi

echo
echo "Done. ${HOME}/bin/recent-sessions-digest.py and any logs in"
echo "${HOME}/Library/Logs/ are left in place — remove them if desired."
