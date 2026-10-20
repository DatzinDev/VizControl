"""Transform, part 2: amounts to US dollars."""


class RateTable:
    def __init__(self, rates):
        self.rates = rates

    def rate(self, currency):
        return self.rates[currency]  # KeyError for a currency without a rate


def make_to_usd(table):
    """The pipeline step, bound to a rate table."""

    def to_usd(orders):
        return [{**o, "usd": round(o["amount"] * table.rate(o["currency"]), 2)} for o in orders]

    return to_usd
