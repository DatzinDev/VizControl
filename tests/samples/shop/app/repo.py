import json


class OrderRepo:
    def __init__(self):
        self.rows = []

    def add(self, order):
        order.id = 500 + len(self.rows) + 1
        self.rows.append(json.dumps({"id": order.id, "total": str(order.total)}))
        return order.id
