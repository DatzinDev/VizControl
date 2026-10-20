from app import pricing
from app.models import Order
from app.repo import OrderRepo


def iter_valid(items):
    for item in items:
        if item.qty <= 0:
            raise ValueError(f"bad quantity for {item.sku}")
        yield item


class OrderService:
    def __init__(self):
        self.repo = OrderRepo()

    def place_order(self, customer, items):
        order = self._build_order(customer, items)
        self.repo.add(order)
        order.status = "paid"
        return order

    def _build_order(self, customer, items):
        order = Order(customer, list(iter_valid(items)))
        order.total = pricing.compute(order)
        return order
