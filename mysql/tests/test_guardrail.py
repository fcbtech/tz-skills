#!/usr/bin/env python3
"""Unit tests for the table-name guardrail. Runs without a live MySQL."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mysql_helper import extract_table_refs, validate_table_names  # noqa: E402


class ExtractTableRefsTests(unittest.TestCase):
    def test_simple_select(self) -> None:
        self.assertEqual(extract_table_refs("SELECT * FROM auth_user LIMIT 1"), ["auth_user"])

    def test_join(self) -> None:
        sql = "SELECT u.id FROM auth_user u JOIN profile_mgt_userprofile p ON p.user_id = u.id"
        self.assertEqual(extract_table_refs(sql), ["auth_user", "profile_mgt_userprofile"])

    def test_update(self) -> None:
        self.assertEqual(extract_table_refs("UPDATE settings_product SET unit='NOS' WHERE id=1"), ["settings_product"])

    def test_insert(self) -> None:
        self.assertEqual(extract_table_refs("INSERT INTO audit_logs (a) VALUES (1)"), ["audit_logs"])

    def test_delete(self) -> None:
        self.assertEqual(extract_table_refs("DELETE FROM stale_sessions WHERE expired=1"), ["stale_sessions"])

    def test_schema_qualified(self) -> None:
        self.assertEqual(extract_table_refs("SELECT 1 FROM tranzact.auth_user"), ["auth_user"])

    def test_backticked(self) -> None:
        self.assertEqual(extract_table_refs("SELECT 1 FROM `auth_user`"), ["auth_user"])

    def test_dedup(self) -> None:
        sql = "SELECT 1 FROM auth_user a JOIN auth_user b ON a.id = b.id"
        self.assertEqual(extract_table_refs(sql), ["auth_user"])


class ValidateTableNamesTests(unittest.TestCase):
    KNOWN = ["auth_user", "profile_mgt_userprofile", "profile_mgt_company", "settings_product"]

    def _validate(self, sql: str) -> list[str]:
        with patch("mysql_helper.get_known_tables", return_value=self.KNOWN):
            return validate_table_names("mstag-dmz", sql)

    def test_known_table_passes(self) -> None:
        self.assertEqual(self._validate("SELECT * FROM auth_user LIMIT 1"), [])

    def test_unknown_table_fails_with_suggestion(self) -> None:
        errors = self._validate("SELECT * FROM auth_users LIMIT 1")
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown table `auth_users`", errors[0])
        self.assertIn("did you mean", errors[0])
        self.assertIn("auth_user", errors[0])

    def test_unknown_table_no_close_match(self) -> None:
        errors = self._validate("SELECT * FROM zzzz_nothing_like_it LIMIT 1")
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown table `zzzz_nothing_like_it`", errors[0])
        # No close match — no suggestion suffix.
        self.assertNotIn("did you mean", errors[0])

    def test_multiple_tables_one_bad(self) -> None:
        sql = "SELECT u.id FROM auth_user u JOIN profile_mgt_userprofiles p ON p.user_id = u.id"
        errors = self._validate(sql)
        self.assertEqual(len(errors), 1)
        self.assertIn("profile_mgt_userprofiles", errors[0])
        self.assertIn("profile_mgt_userprofile", errors[0])

    def test_case_insensitive_match(self) -> None:
        # Stored as auth_user; query uses AUTH_USER — should pass.
        self.assertEqual(self._validate("SELECT * FROM AUTH_USER LIMIT 1"), [])


if __name__ == "__main__":
    unittest.main()
