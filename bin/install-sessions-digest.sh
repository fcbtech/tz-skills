#!/usr/bin/env bash
# install-sessions-digest.sh — install the sessions-digest launchd job.
#
# What it does (idempotent):
#   1. Copies bin/recent-sessions-digest.py to ~/bin/ (creates if needed).
#   2. Fills the plist template with your $HOME and python3 path.
#   3. Writes ~/Library/LaunchAgents/com.tranzact.sessions-digest.plist.
#   4. (Re)loads the launchd job.
#
# Requirements:
#   - macOS
#   - python3 on PATH
#   - slack skill configured (see "Slack Skill Setup" in README)
#
# Usage:
#   bin/install-sessions-digest.sh
#   bin/install-sessions-digest.sh --dry-run    # show what would happen
#   bin/install-sessions-digest.sh --uninstall  # convenience alias

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT_SRC="${REPO_ROOT}/bin/recent-sessions-digest.py"
PLIST_SRC="${REPO_ROOT}/bin/templates/sessions-digest.plist.template"

SCRIPT_DST="${HOME}/bin/recent-sessions-digest.py"
PLIST_DST="${HOME}/Library/LaunchAgents/com.tranzact.sessions-digest.plist"
LABEL="com.tranzact.sessions-digest"
LOGS_DIR="${HOME}/Library/Logs"

DRY=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)  DRY=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            sed -n '1,20p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 1 ;;
    esac
done

# -------- uninstall path --------
if [[ $UNINSTALL -eq 1 ]]; then
    exec "${REPO_ROOT}/bin/uninstall-sessions-digest.sh" ${DRY:+--dry-run}
fi

# -------- prerequisites --------
if [[ $(uname -s) != "Darwin" ]]; then
    echo "Error: this installer only supports macOS (uses launchd)." >&2
    exit 1
fi

PYTHON=$(command -v python3 || true)
if [[ -z $PYTHON ]]; then
    echo "Error: python3 not found on PATH." >&2
    exit 1
fi

for required in "$SCRIPT_SRC" "$PLIST_SRC"; do
    if [[ ! -f $required ]]; then
        echo "Error: missing repo file: $required" >&2
        exit 1
    fi
done

# -------- preview --------
echo "Sessions digest installer"
echo "  HOME    : $HOME"
echo "  python3 : $PYTHON"
echo "  script  : $SCRIPT_DST"
echo "  plist   : $PLIST_DST"
echo "  label   : $LABEL"
echo "  logs    : $LOGS_DIR/sessions-digest.{log,err.log}"
echo

if [[ $DRY -eq 1 ]]; then
    echo "(--dry-run: not writing anything)"
    exit 0
fi

# -------- write files --------
mkdir -p "${HOME}/bin" "$(dirname "$PLIST_DST")" "$LOGS_DIR"

install -m 0755 "$SCRIPT_SRC" "$SCRIPT_DST"
echo "ok: installed $SCRIPT_DST"

# Fill placeholders. Use | as the sed delimiter so $HOME with slashes is fine.
sed -e "s|__HOME__|${HOME}|g" \
    -e "s|__PYTHON__|${PYTHON}|g" \
    "$PLIST_SRC" > "$PLIST_DST"
chmod 0644 "$PLIST_DST"
echo "ok: wrote $PLIST_DST"

# Validate.
if ! plutil -lint "$PLIST_DST" >/dev/null; then
    echo "Error: plist failed validation; see plutil output above." >&2
    exit 1
fi

# -------- (re)load --------
# `launchctl load -w` is bookmark-equivalent across all supported macOS versions.
# Unload first to be idempotent — load fails if the label is already registered.
if launchctl list "$LABEL" >/dev/null 2>&1; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    echo "ok: unloaded previous instance"
fi
launchctl load -w "$PLIST_DST"
echo "ok: loaded $LABEL"

# Confirm.
if ! launchctl list "$LABEL" >/dev/null 2>&1; then
    echo "Error: launchd did not register the job. Check Console.app for errors." >&2
    exit 1
fi

cat <<EOF

Done. The job will fire at 06:00, 08:00, 10:00, 12:00, 14:00, 16:00, 18:00,
20:00 and 22:00 local time.

Smoke-test manually:
  $SCRIPT_DST                # preview only
  $SCRIPT_DST --notify       # actually post to Slack

Logs:
  $LOGS_DIR/sessions-digest.log
  $LOGS_DIR/sessions-digest.err.log

If Slack posts fail, set up the slack skill first:
  python3 ~/.claude/skills/slack/scripts/slack_helper.py setup

To remove:
  bin/uninstall-sessions-digest.sh
EOF
