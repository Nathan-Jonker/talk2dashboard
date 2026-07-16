from __future__ import annotations

from typing import Any


class ContractError(RuntimeError):
    """Machine-readable domain error that may cross the agent tool boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": self.details,
            "retryable": self.retryable,
        }


class VersionConflictError(ContractError):
    def __init__(self, expected_version: int, current_version: int) -> None:
        super().__init__(
            "VERSION_CONFLICT",
            "Dashboard is intussen gewijzigd. Inspecteer de actuele dashboardstatus en probeer eenmaal opnieuw.",
            details={
                "expected_version": expected_version,
                "current_version": current_version,
                "retry_instruction": "inspect_workspace(sections=['dashboard'], detail='compact')",
                "max_retries": 1,
            },
            retryable=True,
        )


class InsufficientBaselineError(ContractError):
    def __init__(self, available_days: float, required_days: float = 14.0) -> None:
        super().__init__(
            "INSUFFICIENT_BASELINE",
            "Er is onvoldoende historische data voor een betrouwbare baseline.",
            details={
                "available_days": round(max(available_days, 0.0), 2),
                "required_days": required_days,
                "alternatives": ["absolute_value", "short_window_trend", "compare_sources"],
            },
            retryable=False,
        )


class InsufficientSeriesError(ContractError):
    def __init__(self, available_pairs: int, required_pairs: int = 3) -> None:
        super().__init__(
            "INSUFFICIENT_SERIES",
            "Er zijn onvoldoende gekoppelde waarden voor correlatie.",
            details={
                "available_pairs": available_pairs,
                "required_pairs": required_pairs,
            },
            retryable=False,
        )
