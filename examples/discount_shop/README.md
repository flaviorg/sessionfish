# discount_shop

A tiny fixture for the sessionfish demo. `shop.discount.order_total` should give 10% off
orders of ten items or more, but the comparison is off by one, so
`tests/test_discount.py::test_bulk_order_gets_ten_percent` fails at the base commit.

## Usage

```python
from decimal import Decimal
from shop.discount import order_total

order_total(Decimal("10.00"), 10)
```
