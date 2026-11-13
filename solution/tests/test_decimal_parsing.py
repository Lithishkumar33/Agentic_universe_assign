from __future__ import annotations

import pytest
from solution.schema import parse_decimal


class TestDecimalParsing:
    def test_plain_numbers(self):
        assert parse_decimal("12000") == 12000.0
        assert parse_decimal("12000.5") == 12000.5
        assert parse_decimal("12000.567") == 12000.57
        assert parse_decimal("0") == 0.0

    def test_rupee_symbol_prefix(self):
        assert parse_decimal("₹12,000") == 12000.0
        assert parse_decimal("₹ 27,554.50") == 27554.5
        assert parse_decimal("₹0") == 0.0

    def test_rs_and_rs_dot_prefix(self):
        assert parse_decimal("Rs. 1,000") == 1000.0
        assert parse_decimal("rs. 1000.25") == 1000.25
        assert parse_decimal("Rs 15000") == 15000.0
        assert parse_decimal("RS 500") == 500.0

    def test_inr_prefix(self):
        assert parse_decimal("INR 25301.4") == 25301.4
        assert parse_decimal("inr 500") == 500.0
        assert parse_decimal("InR 12,500.00") == 12500.0

    def test_negative_values(self):
        assert parse_decimal("-100") is None
        assert parse_decimal("₹-50") is None

    def test_invalid_strings(self):
        assert parse_decimal("N/A") is None
        assert parse_decimal("-") is None
        assert parse_decimal("twelve thousand") is None
        assert parse_decimal("") is None
        assert parse_decimal(None) is None
