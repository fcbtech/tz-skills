import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "sdlc_helper",
    pathlib.Path(__file__).parent.parent / "scripts" / "sdlc_helper.py",
)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

IDS = {
    "project_id": "PID",
    "project_fields": {"Status": {"id": "FSTATUS", "options": {"WIP": "OWIP"}}},
    "org_fields": {"Subtype": {"id": "FSUB", "options": {"dev": "ODEV"}}},
    "issue_types": {"task": "TTASK"},
}


def test_org_field_mutation_vars():
    mut, vars = H.org_field_mutation("ISSUE1", IDS, "Subtype", "dev")
    assert vars == {"iss": "ISSUE1", "f": "FSUB", "o": "ODEV"}
    assert "setIssueFieldValue" in mut


def test_project_field_mutation_vars():
    mut, vars = H.project_field_mutation("ITEM1", IDS, "Status", "WIP")
    assert vars == {"p": "PID", "i": "ITEM1", "f": "FSTATUS", "o": "OWIP"}
    assert "updateProjectV2ItemFieldValue" in mut
