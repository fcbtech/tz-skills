"""Seed production machine-planning masters: operators, workstation groups,
workstations, and routing operations.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. STDLIB + requests ONLY. Designed to be uploaded
directly to a Lambda function (or any host with `requests` available).

What it does
------------
Creates the account-level masters that "machine planning / routing" needs,
from four optional data.md sections:

    [[OPERATOR]]          code, name, wage_per_hr, (comment)
    [[WORKSTATION_GROUP]] name, cost_per_hr, (operator=code), (hours/days), (comment)
    [[WORKSTATION]]       name, group=<group name>, cost_per_hr, (operator), (hours/days/maintenance), (comment)
    [[ROUTING_OP]]        number, name, (desc), (workstation_group=<name>), (workstation=<name>), (operator=<code>)

Each section is seeded in dependency order (operators → groups → workstations →
routing ops) so that references resolve. Everything is idempotent: operators
dedupe by `code`, groups/workstations by `name`, routing ops by `number` — an
entry that already exists is skipped. Step 004 then attaches these routing ops
to BOM rows.

Sequencing
----------
Phase-1 step (despite the 017 number) that must run **before 004** (the BOM
lane), because 004 references the routing ops this step creates. It only needs
the account to exist (step 000). It is a no-op when none of the four sections
are present in data.md.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

import requests


# --- data.md loader ---------------------------------------------------------


def load_data_md(script_file: str) -> dict[str, Any]:
    import tomllib

    data_path = Path(script_file).resolve().parent / "data.md"
    text = data_path.read_text(encoding="utf-8")
    match = re.search(r"```toml\s*\n(.*?)\n```", text, re.DOTALL)
    if not match:
        sys.exit(f"data.md at {data_path} has no ```toml block")
    return tomllib.loads(match.group(1))


DATA = load_data_md(__file__)

BASE_URL: str = DATA["BASE_URL"].rstrip("/")
TIMEOUT = 30

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Tz-Request-Source": "webapp",
}

# Shift defaults applied to workstation groups / workstations when data.md omits them.
DEFAULT_START_TIME = "10:00"
DEFAULT_END_TIME = "18:00"
DEFAULT_WORKING_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --- Logging ----------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("automation")


# --- Auth -------------------------------------------------------------------


def login() -> str:
    url = f"{BASE_URL}/main/login/password-login/"
    log.info(">>> POST /main/login/password-login/")
    response = requests.post(
        url,
        json={"email": DATA["EMAIL"], "password": DATA["PASSWORD"]},
        headers=DEFAULT_HEADERS,
        timeout=TIMEOUT,
    )
    log.info("<<< POST /main/login/password-login/ -> %d", response.status_code)
    if response.status_code >= 400:
        sys.exit(f"Login failed (HTTP {response.status_code}): {response.text[:300]}")
    data = (response.json() or {}).get("data") or {}
    token = data.get("access_token") or data.get("access")
    if not token:
        sys.exit(f"Login response missing access token. Keys: {list(data.keys())}")
    log.info("Login complete; access token captured.")
    return token


# --- HTTP helpers -----------------------------------------------------------


def _auth_headers(token: str) -> dict[str, str]:
    return {**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"}


def _get(token: str, path: str, params: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> GET %s", path)
    response = requests.get(url, params=params, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< GET %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"GET {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


def _post(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> POST %s", path)
    response = requests.post(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< POST %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"POST {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


def _list(token: str, noun: str) -> list[dict]:
    """GET /api/v3/production/<noun>/ → the list of master records."""
    res = _get(token, f"/api/v3/production/{noun}/")
    data = res.get("data")
    # responses seen as either {data:[...]} or {data:{data:[...]}}
    if isinstance(data, dict):
        data = data.get("data") or data.get("results") or []
    return data if isinstance(data, list) else []


# --- Helpers ----------------------------------------------------------------


def _as_list(raw: Any, section: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        sys.exit(f"data.md [[{section}]] must be an array of tables")
    return raw


def _require(entry: dict, keys: list[str], section: str) -> None:
    missing = [k for k in keys if entry.get(k) in (None, "")]
    if missing:
        sys.exit(f"data.md [[{section}]] entry {entry!r} missing required keys: {missing}")


# --- Seeders ----------------------------------------------------------------


def seed_operators(token: str) -> dict[str, str]:
    """Create operators from [[OPERATOR]]. Returns {code: operator_id} for all (existing + new)."""
    specs = _as_list(DATA.get("OPERATOR"), "OPERATOR")
    existing = {o.get("operator_code"): o["id"] for o in _list(token, "operators") if o.get("operator_code")}
    to_create = []
    for s in specs:
        _require(s, ["code", "name"], "OPERATOR")
        if s["code"] in existing:
            log.info("Operator already exists, skipping: %s", s["code"])
            continue
        to_create.append({
            "name": str(s["name"]),
            "operator_code": str(s["code"]),
            "wage_per_hr": s.get("wage_per_hr", 0),
            "comment": str(s.get("comment") or ""),
        })
    if to_create:
        _post(token, "/api/v3/production/operators/bulk", {"operators": to_create})
        log.info("Created %d operator(s).", len(to_create))
        existing = {o.get("operator_code"): o["id"] for o in _list(token, "operators") if o.get("operator_code")}
    return existing


def _shift_fields(spec: dict) -> dict:
    return {
        "operating_start_time": str(spec.get("start_time") or DEFAULT_START_TIME),
        "operating_end_time": str(spec.get("end_time") or DEFAULT_END_TIME),
        "working_days": list(spec.get("working_days") or DEFAULT_WORKING_DAYS),
    }


def seed_workstation_groups(token: str, operators: dict[str, str]) -> dict[str, str]:
    """Create workstation groups from [[WORKSTATION_GROUP]]. Returns {name: group_id}."""
    specs = _as_list(DATA.get("WORKSTATION_GROUP"), "WORKSTATION_GROUP")
    existing = {g["name"]: g["id"] for g in _list(token, "workstation-groups") if g.get("name")}
    to_create = []
    for s in specs:
        _require(s, ["name"], "WORKSTATION_GROUP")
        if s["name"] in existing:
            log.info("Workstation group already exists, skipping: %s", s["name"])
            continue
        op_code = s.get("operator")
        to_create.append({
            "name": str(s["name"]),
            "cost_per_hr": s.get("cost_per_hr", 0),
            "operator_id": operators.get(op_code) if op_code else None,
            **_shift_fields(s),
            "comment": str(s.get("comment") or ""),
        })
    if to_create:
        _post(token, "/api/v3/production/workstation-groups/bulk", {"workstation_groups": to_create})
        log.info("Created %d workstation group(s).", len(to_create))
        existing = {g["name"]: g["id"] for g in _list(token, "workstation-groups") if g.get("name")}
    return existing


def seed_workstations(token: str, groups: dict[str, str], operators: dict[str, str]) -> dict[str, str]:
    """Create workstations from [[WORKSTATION]]. Returns {name: workstation_id}."""
    specs = _as_list(DATA.get("WORKSTATION"), "WORKSTATION")
    existing = {w["name"]: w["id"] for w in _list(token, "workstations") if w.get("name")}
    to_create = []
    for s in specs:
        _require(s, ["name", "group"], "WORKSTATION")
        if s["name"] in existing:
            log.info("Workstation already exists, skipping: %s", s["name"])
            continue
        group_id = groups.get(s["group"])
        if not group_id:
            sys.exit(f"[[WORKSTATION]] {s['name']!r} references unknown group {s['group']!r}")
        op_code = s.get("operator")
        to_create.append({
            "name": str(s["name"]),
            "workstation_group_id": group_id,
            "cost_per_hr": s.get("cost_per_hr", 0),
            "operator_id": operators.get(op_code) if op_code else None,
            **_shift_fields(s),
            "last_maintenance_date": s.get("last_maintenance_date"),
            "next_maintenance_date": s.get("next_maintenance_date"),
            "comment": str(s.get("comment") or ""),
        })
    if to_create:
        _post(token, "/api/v3/production/workstations/bulk", {"workstations": to_create})
        log.info("Created %d workstation(s).", len(to_create))
        existing = {w["name"]: w["id"] for w in _list(token, "workstations") if w.get("name")}
    return existing


def seed_routing_ops(token: str, groups: dict[str, str], workstations: dict[str, str],
                     operators: dict[str, str]) -> None:
    """Create routing operations from [[ROUTING_OP]] (idempotent by number)."""
    specs = _as_list(DATA.get("ROUTING_OP"), "ROUTING_OP")
    existing = {r["number"] for r in _list(token, "routings") if r.get("number")}
    to_create = []
    for s in specs:
        _require(s, ["number", "name"], "ROUTING_OP")
        if s["number"] in existing:
            log.info("Routing op already exists, skipping: %s", s["number"])
            continue
        grp = s.get("workstation_group")
        ws = s.get("workstation")
        op_code = s.get("operator")
        if grp and grp not in groups:
            sys.exit(f"[[ROUTING_OP]] {s['number']!r} references unknown workstation_group {grp!r}")
        if ws and ws not in workstations:
            sys.exit(f"[[ROUTING_OP]] {s['number']!r} references unknown workstation {ws!r}")
        to_create.append({
            "number": str(s["number"]),
            "name": str(s["name"]),
            "desc": str(s.get("desc") or ""),
            "workstation_group_id": groups.get(grp) if grp else None,
            "workstation_id": workstations.get(ws) if ws else None,
            "operator_id": operators.get(op_code) if op_code else None,
        })
    if to_create:
        _post(token, "/api/v3/production/routings/bulk", {"routings": to_create})
        log.info("Created %d routing operation(s).", len(to_create))


# --- Main -------------------------------------------------------------------


def main() -> None:
    sections = ("OPERATOR", "WORKSTATION_GROUP", "WORKSTATION", "ROUTING_OP")
    if not any(DATA.get(s) for s in sections):
        log.info("No routing/machine-planning sections in data.md — nothing to seed.")
        return

    log.info("=== Login ===")
    token = login()

    log.info("=== Seeding operators ===")
    operators = seed_operators(token)

    log.info("=== Seeding workstation groups ===")
    groups = seed_workstation_groups(token, operators)

    log.info("=== Seeding workstations ===")
    workstations = seed_workstations(token, groups, operators)

    log.info("=== Seeding routing operations ===")
    seed_routing_ops(token, groups, workstations, operators)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
