from decimal import Decimal

import pytest

from shop.discount import order_total


def test_small_order_has_no_discount():
    assert order_total(Decimal("10.00"), 3) == Decimal("30.00")


def test_bulk_order_gets_ten_percent():
    assert order_total(Decimal("10.00"), 10) == Decimal("90.00")


def test_large_bulk_order():
    assert order_total(Decimal("2.50"), 40) == Decimal("90.00")


def test_rounding_is_half_up():
    assert order_total(Decimal("0.125"), 1) == Decimal("0.13")


def test_negative_quantity_is_rejected():
    with pytest.raises(ValueError):
        order_total(Decimal("1.00"), -1)
