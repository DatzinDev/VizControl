from decimal import Decimal


class Rule:
    def __init__(self, pct):
        self.pct = pct


def load_vip_rules():
    rule = Rule(20)
    rule.pct = 120  # the bug: a VIP override that is way too large
    return rule


def apply_discount(total, rule):
    discount = total * Decimal(rule.pct) / 100
    if discount > total:
        raise ValueError(f"discount {discount:.2f} exceeds total {total:.2f}")
    return total - discount


def compute(order):
    total = sum(item.price * item.qty for item in order.items)
    if order.customer.tier == "vip":
        return apply_discount(total, load_vip_rules())
    return total
