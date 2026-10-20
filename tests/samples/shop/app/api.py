from app.models import Customer, Item
from app.services import OrderService


def post_order(payload):
    customer = Customer(payload["user_id"], payload["email"], payload["tier"])
    items = [Item(*row) for row in payload["items"]]
    order = OrderService().place_order(customer, items)
    return {"id": order.id, "status": order.status, "total": str(order.total)}
