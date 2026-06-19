import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "sdlc_core",
    pathlib.Path(__file__).parent.parent / "scripts" / "sdlc_core.py",
)
sdlc_core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdlc_core)


def test_module_imports():
    assert sdlc_core is not None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Implements fcbtech/pm#123", 123),
        ("body\n\nFixes fcbtech/pm#7 and stuff", 7),
        ("FCBTECH/PM#45", 45),
        ("relates to org/other#99", None),
        ("no reference here", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_pm_ref(text, expected):
    assert sdlc_core.parse_pm_ref(text) == expected


def test_resolve_subtype():
    rm = {"dev": ["alice", "bob"], "qa": ["carol"]}
    assert sdlc_core.resolve_subtype("alice", rm) == "dev"
    assert sdlc_core.resolve_subtype("carol", rm) == "qa"
    assert sdlc_core.resolve_subtype("dan", rm) is None
    assert sdlc_core.resolve_subtype(None, rm) is None
    assert sdlc_core.resolve_subtype("x", {"dev": ["x"], "qa": ["x"]}) is None


def test_review_state_for_event():
    assert sdlc_core.review_state_for_event("opened") == "Waiting for Review"
    assert sdlc_core.review_state_for_event("ready_for_review") == "In Review"
    assert sdlc_core.review_state_for_event("review_requested") == "In Review"
    assert sdlc_core.review_state_for_event("changes_requested") == "Changes Requested"
    assert sdlc_core.review_state_for_event("approved") == "Approved"
    assert sdlc_core.review_state_for_event("merged") == "Merged"
    assert sdlc_core.review_state_for_event("bogus") is None


def test_has_investment_prefix():
    assert sdlc_core.has_investment_prefix("feat: add x") is True
    assert sdlc_core.has_investment_prefix("FIX: y") is True
    assert sdlc_core.has_investment_prefix("add x") is False
    assert sdlc_core.has_investment_prefix("") is False


def test_feature_branch():
    assert sdlc_core.feature_branch("inventory", "batch tracking DB") == "epic-inventory/batch-tracking-db"
    assert sdlc_core.feature_branch("Inventory", "API_work") == "epic-inventory/api-work"


def test_extract_pr_numbers():
    msgs = ["Merge pull request #12 from x", "feat: thing (#34)", "no pr here", "dup (#12)"]
    assert sdlc_core.extract_pr_numbers(msgs) == [12, 34]
    assert sdlc_core.extract_pr_numbers([]) == []
    assert sdlc_core.extract_pr_numbers(None) == []


def test_merge_transition():
    assert sdlc_core.merge_transition("task", "dev") == {"Status": "Done"}
    assert sdlc_core.merge_transition("bug", "qa-bug") == {"QA State": "Ready for QA"}
    assert sdlc_core.merge_transition("bug", "production-bug") == {"QA State": "Ready for QA"}
    assert sdlc_core.merge_transition("task", "qa") == {}
    assert sdlc_core.merge_transition("epic", None) == {}


def test_release_stage_for_branch():
    assert sdlc_core.release_stage_for_branch("canary") == "In Canary"
    assert sdlc_core.release_stage_for_branch("main") == "In Prod"
    assert sdlc_core.release_stage_for_branch("develop") is None
