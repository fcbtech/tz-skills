#!/usr/bin/env bash
# notify-investigation.sh
# Format an investigation result block and (optionally) post it to Slack.
#
# Posting is OPT-IN. By default this script prints the formatted message
# to stdout and exits — it does NOT post unless --notify is passed.
# This matches the orchestrator's "investigations print only" default;
# the polling loop (or a user) explicitly opts in to Slack delivery.
#
# Reads the diagnosis body from a file or stdin so the orchestrator can
# pipe its formatted output through this wrapper without re-marshalling.
#
# Usage:
#   # Preview the formatted Slack message (no post)
#   echo "diagnosis text" | scripts/notify-investigation.sh --ticket-id 17268
#
#   # Actually post (explicit opt-in)
#   echo "diagnosis text" | scripts/notify-investigation.sh --ticket-id 17268 --notify --channel "#oncall"
#
#   # Threaded reply (Web API only)
#   scripts/notify-investigation.sh --file diagnosis.md --ticket-id 17268 --notify --channel "#oncall" --thread-ts 1700000000.000100

set -euo pipefail

USAGE="Usage: $(basename "$0") --ticket-id <id> [options]

Required:
  --ticket-id <id>          Freshdesk ticket id (used in the message header)

Input (one required):
  --file <path>             Read diagnosis body from a file
  (or pipe diagnosis on stdin)

Optional:
  --notify                  Actually POST to Slack (default is print-only)
  --channel <name|id>       Slack channel. Falls back to SLACK_DEFAULT_CHANNEL.
  --thread-ts <ts>          Reply in the given Slack thread (Web API only)
  --headline <text>         Short summary line (defaults to a generic one)

Notes:
  - Posting is OPT-IN. Without --notify, this script formats and prints
    the message but does NOT touch Slack. Requires explicit opt-in to
    avoid accidental notifications during interactive use.
  - Threading requires SLACK_BOT_TOKEN (the slack helper enforces this)."

TICKET_ID=""
CHANNEL=""
THREAD_TS=""
HEADLINE=""
FILE=""
NOTIFY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ticket-id) TICKET_ID=$2; shift 2 ;;
        --channel)   CHANNEL=$2;   shift 2 ;;
        --thread-ts) THREAD_TS=$2; shift 2 ;;
        --headline)  HEADLINE=$2;  shift 2 ;;
        --file)      FILE=$2;      shift 2 ;;
        --notify)    NOTIFY=1;     shift ;;
        -h|--help)   echo "$USAGE"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; echo "$USAGE" >&2; exit 1 ;;
    esac
done

if [[ -z $TICKET_ID ]]; then
    echo "Error: --ticket-id is required." >&2
    echo "$USAGE" >&2
    exit 1
fi

# Pull diagnosis body from --file or stdin.
if [[ -n $FILE ]]; then
    BODY=$(cat "$FILE")
else
    if [[ -t 0 ]]; then
        echo "Error: provide diagnosis via --file or pipe on stdin." >&2
        exit 1
    fi
    BODY=$(cat)
fi

if [[ -z ${HEADLINE} ]]; then
    HEADLINE="Investigation complete for FD#${TICKET_ID}"
fi

# Build the message. Slack mrkdwn understands a subset of Markdown.
MESSAGE=$(cat <<EOF
*${HEADLINE}*

${BODY}

_FD#${TICKET_ID} · posted by oncall-agent_
EOF
)

# Always print to stdout — that's the baseline behavior.
if [[ $NOTIFY -eq 1 ]]; then
    echo "## Posting to Slack"
else
    echo "## Slack preview (use --notify to actually post)"
fi
echo "channel:   ${CHANNEL:-<SLACK_DEFAULT_CHANNEL>}"
echo "thread-ts: ${THREAD_TS:-<none>}"
echo "---"
echo "$MESSAGE"
echo "---"

if [[ $NOTIFY -eq 0 ]]; then
    echo "(no --notify: not posting)"
    exit 0
fi

# Locate the slack helper.
SLACK_HELPER=""
for candidate in \
    "$HOME/.claude/skills/slack/scripts/slack_helper.py" \
    "$HOME/Work/tranzact/tz-skills/slack/scripts/slack_helper.py"; do
    if [[ -f $candidate ]]; then
        SLACK_HELPER=$candidate
        break
    fi
done

if [[ -z $SLACK_HELPER ]]; then
    echo "Error: slack_helper.py not found. Install the slack skill first." >&2
    exit 1
fi

# Pick the right subcommand. Threading needs the Web API; otherwise let
# the helper auto-pick.
SUBCMD="post"
HELPER_ARGS=()
if [[ -n $THREAD_TS ]]; then
    SUBCMD="post-message"
    HELPER_ARGS+=("--thread-ts" "$THREAD_TS")
fi
if [[ -n $CHANNEL ]]; then
    HELPER_ARGS+=("--channel" "$CHANNEL")
fi

echo "$MESSAGE" | python3 "$SLACK_HELPER" $SUBCMD "${HELPER_ARGS[@]}" --pretty
