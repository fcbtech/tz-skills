#!/usr/bin/env bash
# uninstall-sessions-digest.sh — remove the session-digest launchd jobs.
#
# Unloads both jobs (sessions-digest + sessions-scrum) and removes their
# plists. Leaves the scripts in ~/bin/ and any logs in ~/Library/Logs/ alone
# (delete those yourself if you want).
#
# Usage:
#   bin/uninstall-sessions-digest.sh
#   bin/uninstall-sessions-digest.sh --dry-run

set -euo pipefail

LA_DIR="${HOME}/Library/LaunchAgents"
LABELS=(com.tranzact.sessions-digest com.tranzact.sessions-scrum)

DRY=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '1,12p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 1 ;;
    esac
done

did_anything=0
for label in "${LABELS[@]}"; do
    plist="${LA_DIR}/${label}.plist"
    loaded=0
    launchctl list "$label" >/dev/null 2>&1 && loaded=1

    if [[ $loaded -eq 0 && ! -f $plist ]]; then
        continue
    fi
    did_anything=1

    if [[ $DRY -eq 1 ]]; then
        echo "Would unload $label and remove $plist"
        continue
    fi

    if [[ $loaded -eq 1 ]]; then
        launchctl unload "$plist" 2>/dev/null || true
        echo "ok: unloaded $label"
    fi
    if [[ -f $plist ]]; then
        rm -f "$plist"
        echo "ok: removed $plist"
    fi
done

if [[ $did_anything -eq 0 ]]; then
    echo "nothing to do: neither job is loaded or present"
    exit 0
fi

if [[ $DRY -eq 0 ]]; then
    echo
    echo "Done. ~/bin/claude-digest.py, ~/bin/recent-sessions-digest.py and logs in"
    echo "${HOME}/Library/Logs/ are left in place — remove them if desired."
fi
