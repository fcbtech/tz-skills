#!/usr/bin/env bash
# user-impersonation-lookup.sh
# Build the "log in locally as this user" block from mstag-dmz.
# Runs auth-user-by-id.sql via the mysql skill helper so the table-name
# guardrail applies. Output is structured (## auth_user / ## profile) so
# the parent skill's output formatter can consume it.
#
# Usage:
#   scripts/user-impersonation-lookup.sh --user-id <id>
#   scripts/user-impersonation-lookup.sh --email <email>      # resolves user_id first
#
# Requires: python3, mysql skill installed, ~/.tz-oncall/mstag-dmz.cnf.

set -euo pipefail

USAGE="Usage: $(basename "$0") (--user-id <id> | --email <email>)

Looks up auth_user + profile on mstag-dmz so the on-call can log in locally
as the affected user. The mstag-dmz snapshot carries scrambled credentials,
so this never exposes real prod auth material."

USER_ID=""
EMAIL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user-id) USER_ID=$2; shift 2 ;;
        --email)   EMAIL=$2;   shift 2 ;;
        -h|--help) echo "$USAGE"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; echo "$USAGE" >&2; exit 1 ;;
    esac
done

if [[ -z $USER_ID && -z $EMAIL ]]; then
    echo "$USAGE" >&2
    exit 1
fi

# Locate the mysql skill helper and the SQL templates.
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

# Pick the mstag-dmz profile (always; impersonation never uses the replica).
MSTAG_PROFILE="mstag-dmz"

# If only email was given, resolve user_id from the prod replica first.
if [[ -z $USER_ID ]]; then
    REPLICA_PROFILE="tz-prod-read-replica"
    if ! python3 "$MYSQL_HELPER" list-profiles 2>/dev/null | grep -qx "$REPLICA_PROFILE"; then
        REPLICA_PROFILE="replica"
    fi
    echo "## Resolving user_id from email"
    USER_ID=$(python3 "$MYSQL_HELPER" run --profile "$REPLICA_PROFILE" \
        --sql "SELECT id FROM auth_user WHERE email = '$EMAIL' LIMIT 1" \
        | awk 'NR==2 {print $1}')
    if [[ -z $USER_ID || $USER_ID == "id" ]]; then
        echo "No user found for email $EMAIL" >&2
        exit 2
    fi
    echo "- email:   $EMAIL"
    echo "- user_id: $USER_ID"
    echo
fi

echo "## auth_user + profile (mstag-dmz)"
echo "(Profile carries scrambled credentials — safe for local login)"
echo
python3 "$MYSQL_HELPER" run \
    --profile "$MSTAG_PROFILE" \
    --file "${SQL_DIR}/auth-user-by-id.sql" \
    --var "USER_ID=${USER_ID}"

cat <<EOF

## Log in as

1. Set your local backend's DATABASE_URL to the mstag-dmz snapshot, OR import
   the snapshot locally.
2. Use the email shown above. The mstag-dmz password is scrambled per a
   deterministic local-dev rule (ask team; never echo).
3. Reproduce the ticket flow.
EOF
