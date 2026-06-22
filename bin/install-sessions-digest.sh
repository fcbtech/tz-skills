#!/usr/bin/env bash
# install-sessions-digest.sh — install the Claude-summarized session crons.
#
# Installs TWO launchd jobs (idempotent):
#   com.tranzact.sessions-digest  — every 2h, 06:00-22:00 local: a 4-5 bullet
#                                   "what am I working on" Claude summary → Slack.
#   com.tranzact.sessions-scrum   — weekdays 10:00 local: a standup-formatted
#                                   summary of the last ~3 days → Slack.
#
# Both call ~/bin/claude-digest.py, which reads ~/.claude/projects transcripts,
# redacts secrets, asks `claude --print` to summarize, and posts via the slack skill.
#
# Requirements:
#   - macOS
#   - python3 on PATH
#   - the `claude` CLI installed (Claude Code) and logged in
#   - slack skill configured (see "Slack Skill Setup" in README)
#
# Usage:
#   bin/install-sessions-digest.sh
#   bin/install-sessions-digest.sh --dry-run    # show what would happen
#   bin/install-sessions-digest.sh --uninstall  # convenience alias

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOGS_DIR="${HOME}/Library/Logs"
LA_DIR="${HOME}/Library/LaunchAgents"

# Scripts copied into ~/bin/.
DIGEST_SRC="${REPO_ROOT}/bin/claude-digest.py"
RAW_SRC="${REPO_ROOT}/bin/recent-sessions-digest.py"   # raw no-LLM fallback, kept available

# Jobs: "label|template-name"
JOBS=(
    "com.tranzact.sessions-digest|sessions-digest.plist.template"
    "com.tranzact.sessions-scrum|sessions-scrum.plist.template"
)

DRY=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)  DRY=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '1,24p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 1 ;;
    esac
done

if [[ $UNINSTALL -eq 1 ]]; then
    exec "${REPO_ROOT}/bin/uninstall-sessions-digest.sh" ${DRY:+--dry-run}
fi

# -------- prerequisites --------
if [[ $(uname -s) != "Darwin" ]]; then
    echo "Error: this installer only supports macOS (uses launchd)." >&2
    exit 1
fi

PYTHON=$(command -v python3 || true)
[[ -z $PYTHON ]] && { echo "Error: python3 not found on PATH." >&2; exit 1; }

if ! command -v claude >/dev/null 2>&1 && [[ ! -x "${HOME}/.local/bin/claude" ]]; then
    echo "Warning: \`claude\` CLI not found on PATH or at ~/.local/bin/claude." >&2
    echo "         The crons need it to generate summaries. Install Claude Code first." >&2
fi

for required in "$DIGEST_SRC" "$RAW_SRC"; do
    [[ -f $required ]] || { echo "Error: missing repo file: $required" >&2; exit 1; }
done
for job in "${JOBS[@]}"; do
    tmpl="${REPO_ROOT}/bin/templates/${job#*|}"
    [[ -f $tmpl ]] || { echo "Error: missing template: $tmpl" >&2; exit 1; }
done

# -------- preview --------
echo "Sessions digest installer"
echo "  HOME    : $HOME"
echo "  python3 : $PYTHON"
echo "  scripts : ${HOME}/bin/claude-digest.py  (+ recent-sessions-digest.py fallback)"
for job in "${JOBS[@]}"; do
    echo "  job     : ${job%%|*}"
done
echo "  logs    : $LOGS_DIR/sessions-{digest,scrum}.{log,err.log}"
echo

if [[ $DRY -eq 1 ]]; then
    echo "(--dry-run: not writing anything)"
    exit 0
fi

# -------- write scripts --------
mkdir -p "${HOME}/bin" "$LA_DIR" "$LOGS_DIR"
install -m 0755 "$DIGEST_SRC" "${HOME}/bin/claude-digest.py"
install -m 0755 "$RAW_SRC"    "${HOME}/bin/recent-sessions-digest.py"
echo "ok: installed ~/bin/claude-digest.py (+ recent-sessions-digest.py)"

# -------- write + (re)load each job --------
for job in "${JOBS[@]}"; do
    label="${job%%|*}"
    tmpl="${REPO_ROOT}/bin/templates/${job#*|}"
    dst="${LA_DIR}/${label}.plist"

    sed -e "s|__HOME__|${HOME}|g" -e "s|__PYTHON__|${PYTHON}|g" "$tmpl" > "$dst"
    chmod 0644 "$dst"
    if ! plutil -lint "$dst" >/dev/null; then
        echo "Error: plist failed validation: $dst" >&2
        exit 1
    fi

    if launchctl list "$label" >/dev/null 2>&1; then
        launchctl unload "$dst" 2>/dev/null || true
    fi
    launchctl load -w "$dst"
    launchctl list "$label" >/dev/null 2>&1 \
        && echo "ok: loaded $label" \
        || { echo "Error: launchd did not register $label." >&2; exit 1; }
done

cat <<EOF

Done. Two jobs are live:
  • com.tranzact.sessions-digest — every 2h, 06:00-22:00 local (4-5 bullet summary)
  • com.tranzact.sessions-scrum  — weekdays 10:00 local (standup summary, last 72h)

Smoke-test manually:
  ~/bin/claude-digest.py --mode recent --hours 2  --dry-run    # show prompt only
  ~/bin/claude-digest.py --mode recent --hours 2  --notify     # post to Slack
  ~/bin/claude-digest.py --mode scrum  --hours 72 --notify

Logs:
  $LOGS_DIR/sessions-digest.{log,err.log}
  $LOGS_DIR/sessions-scrum.{log,err.log}

Slack delivery uses the slack skill's .env (bot token + SLACK_DEFAULT_CHANNEL).
Set it up with: python3 ~/.claude/skills/slack/scripts/slack_helper.py setup

To remove both jobs:
  bin/uninstall-sessions-digest.sh
EOF
