"""Hand-written deterministic fixture, NOT cloud-generated tests."""
import pytest
from product.pricing import total


@pytest.mark.parametrize("amount,member,coupon,expected", [
    (1000, False, None, 1000), (1000, False, "SAVE10", 900),
    (10000, True, None, 9500), (9999, True, None, 9999),
    (10000, True, "SAVE10", 8500), (10000, False, None, 10000),
    (0, True, "SAVE10", 0), (101, False, "SAVE10", 90),
])
def test_discount_requirements(amount, member, coupon, expected):
    assert total(amount, member, coupon) == expected


def test_negative():
    with pytest.raises(ValueError, match="negative amount"):
        total(-1, False)


def test_invalid_coupon():
    with pytest.raises(ValueError, match="unknown coupon"):
        total(1000, False, "BOGUS")
