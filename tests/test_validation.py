from __future__ import annotations

import pytest

from cyphercircle.errors import ValidationError
from cyphercircle.validation import (
    normalize_email,
    normalize_search_query,
    normalize_username,
    validate_bio,
    validate_limit,
    validate_location,
    validate_name,
    validate_password,
)


def test_username_and_email_normalization() -> None:
    assert normalize_username("  Alice.A_001  ") == "alice.a_001"
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"


@pytest.mark.parametrize(
    "value",
    ["", "ab", "has space", "bad/slash", "x" * 31],
)
def test_username_validation_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError):
        normalize_username(value)


def test_registration_field_limits() -> None:
    assert validate_name(" Alice ") == "Alice"
    assert validate_bio("x" * 160) == "x" * 160
    assert validate_password("password") == "password"
    with pytest.raises(ValidationError, match="name cannot be empty"):
        validate_name("  ")
    with pytest.raises(ValidationError, match="at most 160"):
        validate_bio("x" * 161)
    with pytest.raises(ValidationError, match="at least 8"):
        validate_password("short")


@pytest.mark.parametrize("field", [validate_name, validate_bio, validate_location])
def test_profile_text_fields_reject_line_breaks(field) -> None:
    with pytest.raises(ValidationError, match="single line"):
        field("safe\nforged menu")


def test_search_and_limit_validation() -> None:
    assert normalize_search_query("  AL  ") == "al"
    assert validate_limit(10) == 10
    with pytest.raises(ValidationError, match="at least 2"):
        normalize_search_query(" a ")
    with pytest.raises(ValidationError, match="between 1"):
        validate_limit(0)
