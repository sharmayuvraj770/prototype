"""Tool registry. Empty in Task 1; ToolSpecs are added by later tasks."""


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, object] = {}

    def names(self) -> list[str]:
        return sorted(self._tools)
