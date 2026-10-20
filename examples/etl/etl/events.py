"""A tiny event bus: parts of the app register handlers, the pipeline emits events (the observer pattern)."""

from collections import defaultdict


class EventBus:
    def __init__(self):
        self.handlers = defaultdict(list)

    def on(self, name, handler):
        self.handlers[name].append(handler)

    def emit(self, name, data):
        for handler in self.handlers[name]:
            handler(data)
