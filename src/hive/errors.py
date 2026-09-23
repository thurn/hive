"""Actionable failures shared by adapters and domain operations."""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "InvalidInput"
    INVALID_RECORD = "InvalidRecord"
    PROVIDER_UNAVAILABLE = "ProviderUnavailable"
    BUSY = "Busy"
    PAUSED = "Paused"
    NOT_FOUND = "NotFound"
    CAPACITY_FULL = "CapacityFull"
    DEPENDENCY_BLOCKED = "DependencyBlocked"
    NO_READY_WORK = "NoReadyWork"
    ALREADY_OWNED = "AlreadyOwned"
    STALE_OWNER = "StaleOwner"
    RECOVERY_REQUIRED = "RecoveryRequired"
    DELIVERY_TIMEOUT = "DeliveryTimeout"
    VALIDATION_FAILED = "ValidationFailed"
    MERGE_CONFLICT = "MergeConflict"
    CANCELLED = "Cancelled"
    SYNCHRONIZATION_REQUIRED = "SynchronizationRequired"
    UNRESOLVED_OUTCOME = "UnresolvedOutcome"


class HiveError(Exception):
    """Known refusal or failure, with honest knowledge of the attempted effect."""

    code: ErrorCode
    detail: str
    uncertain: bool

    def __init__(
        self, code: ErrorCode, detail: str, *, uncertain: bool = False
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.uncertain = uncertain
