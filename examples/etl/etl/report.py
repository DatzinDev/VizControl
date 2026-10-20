"""What the run prints: progress as the pipeline goes, and a summary at the end."""


class Progress:
    def __init__(self):
        self.lines = []

    def on_step_done(self, event):
        self.lines.append(f"{event['step']}: {event['items']} items")
        print(self.lines[-1])


def summarize(repo):
    for customer, usd in repo.totals():
        print(f"{customer:<10} {usd:>10.2f} USD")
