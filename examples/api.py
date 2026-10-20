"""A small FastAPI service. Every request is traced one at a time (every=1), into .vizcontrol/runs:

    uv pip install fastapi uvicorn
    uvicorn examples.api:app
    curl localhost:8000/orders/7
    curl localhost:8000/orders/7/invoice      # a sync endpoint, run in a worker thread
    curl localhost:8000/orders/0              # fails
    vizcontrol serve
"""

import asyncio

from fastapi import FastAPI

from vizcontrol.web import ASGIMiddleware

app = FastAPI()
app.add_middleware(ASGIMiddleware, every=1)


async def load_order(oid):
    await asyncio.sleep(0.01)  # a database
    if oid == 0:
        raise LookupError("no order 0")
    return {"id": oid, "items": [{"sku": "A", "qty": 2, "price": 120}, {"sku": "B", "qty": 1, "price": 80}]}


async def load_customer(oid):
    await asyncio.sleep(0.01)  # another service
    return {"id": oid * 100, "tier": "vip" if oid % 2 else "basic"}


def total(order, customer):
    amount = sum(i["qty"] * i["price"] for i in order["items"])
    return amount * 0.9 if customer["tier"] == "vip" else amount


@app.get("/orders/{oid}")
async def get_order(oid: int):
    order, customer = await asyncio.gather(load_order(oid), load_customer(oid))  # two tasks at once
    return {**order, "customer": customer, "total": total(order, customer)}


@app.get("/orders/{oid}/invoice")
def invoice(oid: int):
    return {"order": oid, "lines": [f"{sku} x{qty}" for sku, qty in (("A", 2), ("B", 1))]}
