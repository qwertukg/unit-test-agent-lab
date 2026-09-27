"""Synthetic discount rules; no corporate source."""


def total(cents: int, member: bool, coupon: str | None = None) -> int:
    if cents < 0:
        raise ValueError("negative amount")
    if coupon not in (None, "SAVE10"):
        raise ValueError("unknown coupon")
    discount = 10 if coupon == "SAVE10" else 0
    if member and cents >= 10000:
        discount += 5
    return cents * (100 - discount) // 100
