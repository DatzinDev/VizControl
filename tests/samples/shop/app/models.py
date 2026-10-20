from decimal import Decimal


class Customer:
    def __init__(self, id, email, tier):
        self.id, self.email, self.tier = id, email, tier


class Item:
    __slots__ = ("sku", "qty", "price")

    def __init__(self, sku, qty, price):
        self.sku, self.qty, self.price = sku, qty, Decimal(price)


class Order:
    def __init__(self, customer, items):
        self.id = None
        self.customer = customer
        self.items = items
        self.status = "draft"
        self.total = Decimal("0")
