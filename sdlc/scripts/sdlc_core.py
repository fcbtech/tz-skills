"""Pure logic for the sdlc skill (no I/O)."""
import re

PM_REF_RE = re.compile(r"\bfcbtech/pm#(\d+)\b", re.IGNORECASE)


def parse_pm_ref(text):
    """Return the fcbtech/pm issue number referenced in text, or None."""
    if not text:
        return None
    m = PM_REF_RE.search(text)
    return int(m.group(1)) if m else None


def resolve_subtype(assignee, role_map):
    """'dev'/'qa' from a role map, or None if unknown/ambiguous."""
    if not assignee:
        return None
    in_dev = assignee in set(role_map.get("dev", []))
    in_qa = assignee in set(role_map.get("qa", []))
    if in_dev and not in_qa:
        return "dev"
    if in_qa and not in_dev:
        return "qa"
    return None


REVIEW_STATE_BY_EVENT = {
    "opened": "Waiting for Review",
    "ready_for_review": "In Review",
    "review_requested": "In Review",
    "changes_requested": "Changes Requested",
    "approved": "Approved",
    "merged": "Merged",
}


def review_state_for_event(event):
    """Map a PR lifecycle event to the SOP Review State, or None."""
    return REVIEW_STATE_BY_EVENT.get(event)


INVESTMENT_PREFIXES = {
    "feat", "fix", "chore", "maint", "dx", "perf", "refactor",
    "debt", "infra", "sec", "docs", "test", "ci", "automation",
}


def has_investment_prefix(title):
    """True if title starts with a valid investment prefix, e.g. 'feat: ...'."""
    if not title or ":" not in title:
        return False
    return title.split(":", 1)[0].strip().lower() in INVESTMENT_PREFIXES


def feature_branch(epic_slug, task_slug):
    """Build the SOP feature-branch name: epic-<epic>/<task>."""
    def slug(s):
        return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")
    return f"epic-{slug(epic_slug)}/{slug(task_slug)}"
