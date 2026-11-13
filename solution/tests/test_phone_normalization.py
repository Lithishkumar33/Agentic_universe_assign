from __future__ import annotations

import pytest
from solution.crypto import mask_phone, hash_phone
from solution.schema import parse_phone


class TestPhoneNormalization:
    def test_canonical_ten_digit(self):
        assert parse_phone("9876543210") == "+919876543210"
        assert parse_phone("6123456789") == "+916123456789"
        assert parse_phone("7123456789") == "+917123456789"
        assert parse_phone("8123456789") == "+918123456789"

    def test_with_country_code_plus_91(self):
        assert parse_phone("+91 98765 43210") == "+919876543210"
        assert parse_phone("+919876543210") == "+919876543210"
        assert parse_phone("(+91) 9876543210") == "+919876543210"

    def test_with_prefix_91_twelve_digits(self):
        assert parse_phone("91-98765-43210") == "+919876543210"
        assert parse_phone("919876543210") == "+919876543210"

    def test_with_leading_zero_eleven_digits(self):
        assert parse_phone("09876543210") == "+919876543210"
        assert parse_phone("0 98765 43210") == "+919876543210"

    def test_invalid_lengths(self):
        assert parse_phone("987654321") is None       # 9 digits
        assert parse_phone("9876543210123") is None   # 13 digits
        assert parse_phone("") is None
        assert parse_phone(None) is None

    def test_invalid_starting_digit(self):
        # Must start with 6, 7, 8, or 9
        assert parse_phone("5876543210") is None
        assert parse_phone("1876543210") is None
        assert parse_phone("2876543210") is None

    def test_invalid_landline_and_international(self):
        assert parse_phone("022-2451-9999") is None
        assert parse_phone("+1 415 555 0134") is None
        assert parse_phone("N/A") is None

    def test_phone_masking(self):
        assert mask_phone("+919876543210") == "+91******3210"
        assert mask_phone("+916123456789") == "+91******6789"
        assert mask_phone("") == "+91******0000"

    def test_phone_hashing_deterministic(self):
        h1 = hash_phone("+919876543210")
        h2 = hash_phone("+919876543210")
        assert h1 == h2
        assert len(h1) == 64
        assert h1 != hash_phone("+919876543211")
