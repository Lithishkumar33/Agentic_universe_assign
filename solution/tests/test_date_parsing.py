from __future__ import annotations

import pytest
from solution.schema import parse_date


class TestDateParsing:
    def test_iso_format(self):
        assert parse_date("2026-10-15") == "2026-10-15"
        assert parse_date("2026-01-05") == "2026-01-05"

    def test_dd_mm_yyyy_slash(self):
        # Day-first
        assert parse_date("15/10/2026") == "2026-10-15"
        assert parse_date("01/02/2026") == "2026-02-01"  # 1st Feb, not 2nd Jan

    def test_dd_mm_yyyy_hyphen(self):
        assert parse_date("15-10-2026") == "2026-10-15"
        assert parse_date("05-12-2026") == "2026-12-05"

    def test_dd_mon_yyyy_case_insensitive(self):
        assert parse_date("01-Dec-2026") == "2026-12-01"
        assert parse_date("01-dec-2026") == "2026-12-01"
        assert parse_date("01-DEC-2026") == "2026-12-01"
        assert parse_date("15-Oct-2026") == "2026-10-15"
        assert parse_date("28-feb-2026") == "2026-02-28"

    def test_impossible_dates(self):
        assert parse_date("31/02/2026") is None  # Feb 31 does not exist
        assert parse_date("29/02/2026") is None  # 2026 is not a leap year
        assert parse_date("2026-13-01") is None  # Month 13
        assert parse_date("32-01-2026") is None  # Day 32
        assert parse_date("31-Apr-2026") is None # April has 30 days

    def test_non_date_strings(self):
        assert parse_date("soon") is None
        assert parse_date("TBD") is None
        assert parse_date("") is None
        assert parse_date(None) is None
