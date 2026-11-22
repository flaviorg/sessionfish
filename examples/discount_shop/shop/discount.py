"""Order totals with a bulk discount."""

from decimal import ROUND_HALF_UP, Decimal

BULK_THRESHOLD = 10
BULK_RATE = Decimal("0.10")


def order_total(unit_price: Decimal, quantity: int) -> Decimal:
    """Total for ``quantity`` items. Orders of BULK_THRESHOLD items or more get BULK_RATE off."""
    if quantity < 0:
        raise ValueError("quantity must be >= 0")
    total = unit_price * quantity
    if quantity > BULK_THRESHOLD:
        total -= total * BULK_RATE
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
