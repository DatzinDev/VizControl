"""Transform, part 1: tidy each row, drop the ones without a customer, order by id."""


def clean_row(row):
    name = row["customer"].strip()
    if not name:
        return None
    return {"id": int(row["id"]), "customer": name, "amount": float(row["amount"]), "currency": row["currency"]}


def order_id(order):
    return order["id"]


def clean_rows(rows):
    orders = [o for o in map(clean_row, rows) if o is not None]  # map() calls clean_row back for each row
    return sorted(orders, key=order_id)  # sorted() calls order_id back
