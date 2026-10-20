"""The orchestrator: it doesn't know what the steps do, it calls the ones it was given, in order."""


class Pipeline:
    def __init__(self, steps, bus):
        self.steps = steps  # functions: each takes the data so far and returns the next
        self.bus = bus

    def run(self, source):
        data = source
        for step in self.steps:
            data = step(data)  # a callback: which function runs here depends on how the pipeline was built
            self.bus.emit("step_done", {"step": step.__name__, "items": len(data)})
        self.bus.emit("finished", data)
        return data
