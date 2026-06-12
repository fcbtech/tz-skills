#!/usr/bin/env python3
# pyright: reportAny=false
# pyright: reportUnusedCallResult=false
"""
MySQL helper with named profiles and a table-name guardrail.

Profiles live at ~/.tz-oncall/<profile>.cnf (mode 0600). Each is a normal
MySQL `[client]` cnf — host/user/password/database. Profile lookups can be
overridden via MYSQL_PROFILES_DIR.

Every read or write goes through a guardrail that:
  1. extracts identifiers after FROM/JOIN/UPDATE/INSERT INTO/DELETE FROM
  2. validates them against information_schema.tables (24h-cached)
  3. refuses unknown tables and suggests close matches via difflib

This catches hallucinated table names BEFORE the server sees the query.
"""

from __future__ import annotations

import argparse
import difflib
import getpass
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PROFILES_DIR = Path.home() / ".tz-oncall"
SCHEMA_CACHE_DIR_NAME = "schema-cache"
SCHEMA_CACHE_TTL_SECONDS = 24 * 60 * 60

# Identifiers that follow these keywords are real tables (or CTE refs / aliases
# that we can't disambiguate without a parser — see --no-schema-check).
_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+"
    r"(?P<ident>`?[a-zA-Z_][\w$]*`?(?:\s*\.\s*`?[a-zA-Z_][\w$]*`?)?)",
    re.IGNORECASE,
)

# Reserved words that can follow FROM/JOIN/etc. and are NOT table refs.
# `DUAL` is the MySQL anonymous one-row table; everything else here would
# only appear in non-table positions but the regex is intentionally loose.
_NON_TABLE_IDENTS = {"dual", "select"}


def profiles_dir() -> Path:
    override = os.environ.get("MYSQL_PROFILES_DIR")
    return Path(override).expanduser() if override else DEFAULT_PROFILES_DIR


def profile_path(profile: str) -> Path:
    return profiles_dir() / f"{profile}.cnf"


def list_profiles() -> list[str]:
    base = profiles_dir()
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.cnf"))


def schema_cache_path(profile: str) -> Path:
    return profiles_dir() / SCHEMA_CACHE_DIR_NAME / f"{profile}.json"


def require_profile(profile: str) -> Path:
    path = profile_path(profile)
    if not path.exists():
        known = list_profiles()
        suggestion = difflib.get_close_matches(profile, known, n=1, cutoff=0.4)
        hint = f"; did you mean '{suggestion[0]}'?" if suggestion else ""
        sys.exit(
            f"Profile '{profile}' not found at {path}{hint}\n"
            f"Known profiles: {', '.join(known) or '(none)'}\n"
            f"Create one with: python mysql/scripts/mysql_helper.py setup --profile {profile}"
        )
    return path


def run_mysql(profile: str, sql: str, *, extra_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    cnf = require_profile(profile)
    cmd = ["mysql", f"--defaults-extra-file={cnf}", "--batch", "--raw"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(["-e", sql])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def fetch_table_list(profile: str) -> list[str]:
    """Query information_schema.tables for the profile's default database."""
    sql = (
        "SELECT TABLE_NAME FROM information_schema.tables "
        "WHERE TABLE_SCHEMA = DATABASE() "
        "ORDER BY TABLE_NAME"
    )
    result = run_mysql(profile, sql)
    if result.returncode != 0:
        sys.exit(f"Schema fetch failed for profile '{profile}':\n{result.stderr}")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    # First line is the column header in --batch mode.
    return lines[1:] if lines and lines[0] == "TABLE_NAME" else lines


def load_schema_cache(profile: str, *, max_age_seconds: int = SCHEMA_CACHE_TTL_SECONDS) -> list[str] | None:
    path = schema_cache_path(profile)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    fetched_at = payload.get("fetched_at", 0)
    if not isinstance(fetched_at, (int, float)) or time.time() - fetched_at > max_age_seconds:
        return None
    tables = payload.get("tables")
    if isinstance(tables, list) and all(isinstance(t, str) for t in tables):
        return tables
    return None


def save_schema_cache(profile: str, tables: list[str]) -> None:
    path = schema_cache_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    path.write_text(
        json.dumps({"fetched_at": int(time.time()), "tables": tables}, sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def get_known_tables(profile: str, *, force_refresh: bool = False) -> list[str]:
    if not force_refresh:
        cached = load_schema_cache(profile)
        if cached is not None:
            return cached
    tables = fetch_table_list(profile)
    save_schema_cache(profile, tables)
    return tables


def extract_table_refs(sql: str) -> list[str]:
    """Pull every identifier that follows a table-context keyword."""
    refs: list[str] = []
    for match in _TABLE_REF_RE.finditer(sql):
        raw = match.group("ident")
        # Strip backticks and split on schema-qualified `db`.`table`.
        cleaned = raw.replace("`", "").strip()
        if "." in cleaned:
            cleaned = cleaned.split(".", 1)[1].strip()
        if cleaned.lower() in _NON_TABLE_IDENTS:
            continue
        refs.append(cleaned)
    # Dedup preserving order.
    seen: set[str] = set()
    return [r for r in refs if not (r in seen or seen.add(r))]


def validate_table_names(profile: str, sql: str) -> list[str]:
    """Return a list of error strings (empty if everything checks out)."""
    refs = extract_table_refs(sql)
    if not refs:
        return []
    known = get_known_tables(profile)
    known_lower = {t.lower(): t for t in known}
    errors: list[str] = []
    for ref in refs:
        if ref.lower() in known_lower:
            continue
        suggestions = difflib.get_close_matches(ref, known, n=3, cutoff=0.6)
        hint = f"; did you mean: {', '.join(suggestions)}" if suggestions else ""
        errors.append(f"unknown table `{ref}` on profile `{profile}`{hint}")
    return errors


def guardrail(profile: str, sql: str, *, bypass: bool) -> None:
    if bypass:
        return
    errors = validate_table_names(profile, sql)
    if errors:
        sys.stderr.write("Table-name guardrail refused this query:\n")
        for err in errors:
            sys.stderr.write(f"  - {err}\n")
        sys.stderr.write(
            "\nFix the typo, or pass --no-schema-check if the table is created in this query.\n"
            "Refresh the schema cache with: schema-refresh --profile <name>\n"
        )
        sys.exit(2)


def read_sql_input(args: argparse.Namespace) -> str:
    if getattr(args, "sql", None):
        return args.sql
    if getattr(args, "file", None):
        return Path(args.file).expanduser().read_text(encoding="utf-8")
    sys.exit("Provide SQL via --sql '<inline>' or --file <path>.")


def expand_vars(sql: str, var_pairs: list[str] | None) -> str:
    """Apply --var KEY=VALUE substitutions of the form ${KEY} in the SQL."""
    if not var_pairs:
        return sql
    out = sql
    for pair in var_pairs:
        if "=" not in pair:
            sys.exit(f"--var expects KEY=VALUE; got: {pair}")
        key, value = pair.split("=", 1)
        out = out.replace(f"${{{key.strip()}}}", value)
    return out


def cmd_setup(args: argparse.Namespace) -> None:
    base = profiles_dir()
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(stat.S_IRWXU)

    profile = args.profile or input("Profile name (e.g. tz-prod-read-replica, mstag-dmz): ").strip()
    if not profile:
        sys.exit("Profile name is required.")

    path = profile_path(profile)
    if path.exists():
        overwrite = input(f"{path} already exists. Overwrite? [y/N]: ").strip().lower()
        if overwrite not in {"y", "yes"}:
            print("Aborted.")
            return

    host = input("MySQL host: ").strip()
    user = input("MySQL user: ").strip()
    password = getpass.getpass("MySQL password: ").strip()
    database = input("Default database: ").strip()

    if not all((host, user, password, database)):
        sys.exit("host, user, password and database are all required.")

    path.write_text(
        f"[client]\nhost={host}\nuser={user}\npassword={password}\ndatabase={database}\n",
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"Wrote {path}")
    print(f"Verify with: python mysql/scripts/mysql_helper.py run --profile {profile} --sql 'SELECT 1'")


def cmd_list_profiles(_args: argparse.Namespace) -> None:
    profiles = list_profiles()
    if not profiles:
        print(f"No profiles in {profiles_dir()}.")
        return
    for profile in profiles:
        print(profile)


def cmd_run(args: argparse.Namespace) -> None:
    sql = expand_vars(read_sql_input(args), args.var)
    guardrail(args.profile, sql, bypass=args.no_schema_check)
    result = run_mysql(args.profile, sql)
    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    sys.exit(result.returncode)


def cmd_schema_refresh(args: argparse.Namespace) -> None:
    tables = get_known_tables(args.profile, force_refresh=True)
    print(f"Cached {len(tables)} tables for profile '{args.profile}' at {schema_cache_path(args.profile)}")


def cmd_dry_run_write(args: argparse.Namespace) -> None:
    """Wraps the user-supplied DML in BEGIN/SELECT/.../ROLLBACK and runs it."""
    if args.profile != "mstag-dmz":
        sys.exit(
            "Refused: dry-run writes are only allowed on profile 'mstag-dmz'.\n"
            "Production replicas are read-only by policy."
        )

    inner = expand_vars(read_sql_input(args), args.var)
    if "BEGIN" in inner.upper() or "COMMIT" in inner.upper() or "ROLLBACK" in inner.upper():
        sys.exit("Refused: input must NOT contain BEGIN/COMMIT/ROLLBACK — the wrapper adds them.")

    pre_sql = args.pre_select or ""
    post_sql = args.post_select or pre_sql

    wrapped = (
        "START TRANSACTION;\n"
        + (f"-- pre-state\n{pre_sql};\n" if pre_sql else "")
        + f"-- DML\n{inner}\n"
        + (";\n" if not inner.rstrip().endswith(";") else "")
        + "SELECT ROW_COUNT() AS rows_affected;\n"
        + (f"-- post-state\n{post_sql};\n" if post_sql else "")
        + "ROLLBACK;\n"
    )

    guardrail(args.profile, wrapped, bypass=args.no_schema_check)
    print("Dry-run wrapper SQL:")
    print(wrapped)
    print("---")

    result = run_mysql(args.profile, wrapped)
    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    print("---")
    print("Dry-run complete. Review pre-state / rows_affected / post-state above.")
    print("To commit, re-run the SAME DML with: --commit (after explicit user approval)")
    sys.exit(result.returncode)


def cmd_commit_write(args: argparse.Namespace) -> None:
    """Wraps the user-supplied DML in BEGIN/.../COMMIT after dry-run confirmation."""
    if args.profile != "mstag-dmz":
        sys.exit("Refused: writes only on mstag-dmz.")
    inner = expand_vars(read_sql_input(args), args.var)
    if "BEGIN" in inner.upper() or "COMMIT" in inner.upper() or "ROLLBACK" in inner.upper():
        sys.exit("Refused: input must NOT contain BEGIN/COMMIT/ROLLBACK — the wrapper adds them.")

    post_sql = args.post_select or ""
    wrapped = (
        "START TRANSACTION;\n"
        + f"{inner}\n"
        + (";\n" if not inner.rstrip().endswith(";") else "")
        + "SELECT ROW_COUNT() AS rows_affected;\n"
        + (f"{post_sql};\n" if post_sql else "")
        + "COMMIT;\n"
    )
    guardrail(args.profile, wrapped, bypass=args.no_schema_check)
    print("Committing wrapper SQL:")
    print(wrapped)
    print("---")
    result = run_mysql(args.profile, wrapped)
    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    sys.exit(result.returncode)


def add_sql_input_args(p: argparse.ArgumentParser) -> None:
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--sql", help="Inline SQL")
    group.add_argument("--file", help="Path to SQL file")
    p.add_argument("--var", action="append", help="KEY=VALUE substitution for ${KEY} placeholders", default=[])
    p.add_argument("--no-schema-check", action="store_true", help="Bypass the table-name guardrail (last resort)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MySQL helper with named profiles and a table-name guardrail")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Create or replace a profile cnf")
    setup.add_argument("--profile", help="Profile name; prompted if omitted")
    setup.set_defaults(func=cmd_setup)

    profiles = subparsers.add_parser("list-profiles", help="List known profiles in ~/.tz-oncall/")
    profiles.set_defaults(func=cmd_list_profiles)

    run = subparsers.add_parser("run", help="Run SQL on a profile (read or write — DML only if you mean it)")
    run.add_argument("--profile", required=True)
    add_sql_input_args(run)
    run.set_defaults(func=cmd_run)

    refresh = subparsers.add_parser("schema-refresh", help="Force-refresh the table-name cache for a profile")
    refresh.add_argument("--profile", required=True)
    refresh.set_defaults(func=cmd_schema_refresh)

    dry = subparsers.add_parser(
        "dry-run-write",
        help="Wrap your DML in BEGIN/pre-SELECT/DML/ROW_COUNT/post-SELECT/ROLLBACK and run it (mstag-dmz only)",
    )
    dry.add_argument("--profile", required=True)
    dry.add_argument("--pre-select", help="SELECT shown before the DML (e.g. SELECT id, email FROM auth_user WHERE id=42)")
    dry.add_argument("--post-select", help="SELECT shown after the DML; defaults to --pre-select if omitted")
    add_sql_input_args(dry)
    dry.set_defaults(func=cmd_dry_run_write)

    commit = subparsers.add_parser(
        "commit-write",
        help="After dry-run confirmation: re-run the SAME DML wrapped in BEGIN/.../COMMIT (mstag-dmz only)",
    )
    commit.add_argument("--profile", required=True)
    commit.add_argument("--post-select", help="SELECT shown after the DML")
    add_sql_input_args(commit)
    commit.set_defaults(func=cmd_commit_write)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
