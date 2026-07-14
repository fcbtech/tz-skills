#!/usr/bin/env bash
# customer-state-snapshot.sh
# First-60-seconds baseline for any ticket: who is this customer? are they
# active? who owns the account? how many users? when was the last activity?
#
# Calls the mysql skill helper against tz-prod-read-replica so the table-name
# guardrail applies.
#
# Usage: scripts/customer-state-snapshot.sh --company-id <id>
#
# Requires: python3, mysql skill installed, ~/.tz-oncall/tz-prod-read-replica.cnf
# (or replica.cnf as fallback).

set -euo pipefail

USAGE="Usage: $(basename "$0") --company-id <id>

Prints company row, owner(s), active user count, and recent-activity summary
for the given tz_company_id. Read-only."

COMPANY_ID=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --company-id) COMPANY_ID=$2; shift 2 ;;
        -h|--help)    echo "$USAGE"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; echo "$USAGE" >&2; exit 1 ;;
    esac
done

if [[ -z $COMPANY_ID ]]; then
    echo "$USAGE" >&2
    exit 1
fi

# Locate helpers and SQL templates.
SKILL_ROOT=""
for candidate in \
    "$HOME/.claude/skills/oncall-agent" \
    "$HOME/Work/tranzact/tz-skills/oncall-agent"; do
    if [[ -d $candidate ]]; then
        SKILL_ROOT=$candidate
        break
    fi
done

MYSQL_HELPER=""
for candidate in \
    "$HOME/.claude/skills/mysql/scripts/mysql_helper.py" \
    "$HOME/Work/tranzact/tz-skills/mysql/scripts/mysql_helper.py"; do
    if [[ -f $candidate ]]; then
        MYSQL_HELPER=$candidate
        break
    fi
done

if [[ -z $MYSQL_HELPER ]]; then
    echo "Error: mysql_helper.py not found. Install the mysql skill first." >&2
    exit 1
fi

SQL_DIR="${SKILL_ROOT}/references/sql"

# Pick whichever replica profile name exists.
PROFILE="tz-prod-read-replica"
if ! python3 "$MYSQL_HELPER" list-profiles 2>/dev/null | grep -qx "$PROFILE"; then
    PROFILE="replica"
fi

echo "## Company state snapshot — company_id=${COMPANY_ID}"
echo "(Profile: ${PROFILE}; read-only)"
echo
python3 "$MYSQL_HELPER" run \
    --profile "$PROFILE" \
    --file "${SQL_DIR}/company-overview.sql" \
    --var "COMPANY_ID=${COMPANY_ID}"
