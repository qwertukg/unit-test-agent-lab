from agent_lab.contracts import RetryLater


class Actions:
    """Write intent, reconcile external identity, perform once, persist acknowledgement."""

    def __init__(self, store, run_id, guard=lambda: None, limit=3, after_effect=lambda key: None):
        self.store, self.run_id, self.guard = store, run_id, guard
        self.limit, self.after_effect = limit, after_effect

    def perform(self, key, kind, intent, lookup, create):
        self.guard()
        row = self.store.intent(key, self.run_id, kind, intent)
        if row["status"] == "completed":
            return row["result"]
        found = lookup()
        if found is not None:
            self.guard()
            self.store.complete(key, found)
            return found
        self.guard()
        self.store.attempt(key, self.limit)
        result = create()
        self.after_effect(key)  # Fault injection in tests, absent from runtime config.
        self.guard()
        self.store.complete(key, result)
        return result

    def observe_pipeline(self, key, intent, lookup):
        # Immutable verification ref emits ONE push pipeline. Never blindly POST another.
        row = self.store.intent(key, self.run_id, "pipeline", intent)
        if row["status"] == "completed":
            return row["result"]
        result = lookup()
        if result is None:
            raise RetryLater("awaiting automatic push pipeline on immutable verification ref")
        self.guard()
        self.store.complete(key, result)
        return result
