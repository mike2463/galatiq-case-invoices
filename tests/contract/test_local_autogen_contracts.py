"""No-cost AutoGen schema contracts that complement the opt-in live suite."""

import pytest
from autogen_core.tools import FunctionTool


def test_invalid_strict_tool_schema_fails_at_construction() -> None:
    def defaulted_argument(value: int = 1) -> int:
        return value

    with pytest.raises(ValueError, match="Default arguments are not allowed"):
        tool = FunctionTool(
            defaulted_argument,
            description="An intentionally unsupported strict schema.",
            strict=True,
        )
        _ = tool.schema
