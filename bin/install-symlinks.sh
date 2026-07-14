#!/usr/bin/env bash
# install-symlinks.sh — idempotent installer that links each skill in this
# repo into ~/.claude/skills/ so Claude Code can auto-discover them.
#
# Usage:
#   bin/install-symlinks.sh              # link every skill in the repo
#   bin/install-symlinks.sh freshdesk    # link only specific skill(s)
#   bin/install-symlinks.sh --dry-run    # show what would be linked

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET_DIR="${HOME}/.claude/skills"

DRY_RUN=0
SELECTED=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '1,12p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) SELECTED+=("$1"); shift ;;
    esac
done

mkdir -p "$TARGET_DIR"

# Discover skills: any top-level dir containing SKILL.md.
ALL_SKILLS=()
for dir in "$REPO_ROOT"/*/; do
    name=$(basename "$dir")
    [[ -f "${dir}SKILL.md" ]] || continue
    ALL_SKILLS+=("$name")
done

# Default to all skills if none specified.
if [[ ${#SELECTED[@]} -eq 0 ]]; then
    SELECTED=("${ALL_SKILLS[@]}")
fi

EXIT_CODE=0
for name in "${SELECTED[@]}"; do
    src="${REPO_ROOT}/${name}"
    dst="${TARGET_DIR}/${name}"

    if [[ ! -f "${src}/SKILL.md" ]]; then
        echo "skip: $name (no SKILL.md under $src)" >&2
        EXIT_CODE=1
        continue
    fi

    if [[ -L "$dst" ]]; then
        existing=$(readlink "$dst")
        if [[ $existing == "$src" ]]; then
            echo "ok:   $name -> already linked"
            continue
        fi
        echo "warn: $name -> existing symlink points elsewhere ($existing); skipping" >&2
        EXIT_CODE=1
        continue
    fi

    if [[ -e $dst ]]; then
        echo "warn: $name -> $dst exists and is not a symlink; skipping" >&2
        EXIT_CODE=1
        continue
    fi

    if [[ $DRY_RUN -eq 1 ]]; then
        echo "would link: $dst -> $src"
    else
        ln -s "$src" "$dst"
        echo "link: $name -> $dst"
    fi
done

exit $EXIT_CODE
