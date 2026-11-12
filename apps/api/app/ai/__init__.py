class AIError(Exception):
    """All models failed. `attempts` is [(model, short_reason)]; never keys or lead text."""

    def __init__(self, attempts: list[tuple[str, str]]):
        self.attempts = attempts
        super().__init__("; ".join(f"{m}: {r}" for m, r in attempts) or "no models configured")
