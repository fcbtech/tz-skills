#!/usr/bin/env bash
# fetch-ticket-context.sh
# Extract TranZact ids from a Freshdesk ticket: cf_company_id, cf_user_id*,
# plus every other cf_* custom field present. Output is a single ready-to-use
# resolution block that the oncall-agent skill can announce to the user.
#
# Calls the `freshdesk` skill helper for the API call.
#
# Usage: scripts/fetch-ticket-context.sh <ticket-id>
#
# Requires: python3, jq, freshdesk skill installed (~/.claude/skills/freshdesk
# or ~/Work/tranzact/tz-skills/freshdesk), Freshdesk API key configured.

set -euo pipefail

if [[ ${1:-} == "-h" || ${1:-} == "--help" || -z ${1:-} ]]; then
    cat <<EOF
Usage: $(basename "$0") <ticket-id>

Fetches a Freshdesk ticket and prints the resolved TranZact ids.

Example:
  $(basename "$0") 17268
EOF
    exit 0
fi

TICKET_ID=$1

if ! command -v jq >/dev/null 2>&1; then
    echo "Error: jq is required but not on PATH. brew install jq" >&2
    exit 1
fi

# Locate the freshdesk skill helper. Prefer the local skill registry; fall
# back to the cloned repo.
HELPER=""
for candidate in \
    "$HOME/.claude/skills/freshdesk/scripts/freshdesk_helper.py" \
    "$HOME/Work/tranzact/tz-skills/freshdesk/scripts/freshdesk_helper.py"; do
    if [[ -f $candidate ]]; then
        HELPER=$candidate
        break
    fi
done

if [[ -z $HELPER ]]; then
    echo "Error: freshdesk_helper.py not found. Install the freshdesk skill first." >&2
    exit 1
fi

TICKET_JSON=$(python3 "$HELPER" get-ticket "$TICKET_ID" --pretty 2>/dev/null)
if [[ -z $TICKET_JSON ]]; then
    echo "Error: empty response from Freshdesk for ticket $TICKET_ID" >&2
    exit 1
fi

# Pull custom fields and the requester block.
COMPANY_ID=$(jq -r '.custom_fields.cf_company_id // empty' <<<"$TICKET_JSON")
# cf_user_id may have a numeric Freshdesk-internal suffix — match by prefix.
USER_ID=$(jq -r '.custom_fields | with_entries(select(.key | startswith("cf_user_id"))) | first(.[]) // empty' <<<"$TICKET_JSON")
APPROVAL_ID=$(jq -r '.custom_fields.cf_approval_id // empty' <<<"$TICKET_JSON")
ENTITY_ID=$(jq -r '.custom_fields.cf_entity_id // empty' <<<"$TICKET_JSON")
ENTITY_TYPE=$(jq -r '.custom_fields.cf_entity_type // empty' <<<"$TICKET_JSON")
SUBJECT=$(jq -r '.subject // empty' <<<"$TICKET_JSON")
STATUS=$(jq -r '.status // empty' <<<"$TICKET_JSON")
REQUESTER_EMAIL=$(jq -r '.requester.email // empty' <<<"$TICKET_JSON")
COMPANY_NAME=$(jq -r '.company.name // empty' <<<"$TICKET_JSON")

echo "## Ticket FD#${TICKET_ID}"
echo "- Subject: ${SUBJECT}"
echo "- Status:  ${STATUS}"
[[ -n $REQUESTER_EMAIL ]] && echo "- Requester email: ${REQUESTER_EMAIL}"
[[ -n $COMPANY_NAME ]] && echo "- Freshdesk company: ${COMPANY_NAME}"
echo
echo "## Resolved TranZact ids"
if [[ -n $COMPANY_ID ]]; then
    echo "- tz_company_id: ${COMPANY_ID}"
else
    echo "- tz_company_id: (missing — cf_company_id is null/empty; do NOT silently fall back to email)"
fi
if [[ -n $USER_ID ]]; then
    echo "- tz_user_id:    ${USER_ID}"
else
    echo "- tz_user_id:    (missing — no cf_user_id* found; do NOT silently fall back to email)"
fi
[[ -n $APPROVAL_ID ]] && echo "- approval_id:   ${APPROVAL_ID}"
[[ -n $ENTITY_ID ]]   && echo "- entity_id:     ${ENTITY_ID}"
[[ -n $ENTITY_TYPE ]] && echo "- entity_type:   ${ENTITY_TYPE}"

# Surface any other cf_* fields the agent might find useful.
OTHER=$(jq -r '.custom_fields | to_entries | map(select(.key | test("^cf_") and (test("^cf_(company_id|user_id|approval_id|entity_id|entity_type)") | not)) | select(.value != null and .value != "")) | .[] | "- \(.key): \(.value)"' <<<"$TICKET_JSON")
if [[ -n $OTHER ]]; then
    echo
    echo "## Other cf_* fields"
    echo "$OTHER"
fi

# Exit non-zero if BOTH primary ids are missing — caller can branch on this.
if [[ -z $COMPANY_ID && -z $USER_ID ]]; then
    exit 3
fi
