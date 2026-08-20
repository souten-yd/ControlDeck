from __future__ import annotations

from enum import StrEnum

ADDON_CONTRACT_VERSION = "2.0"
BRIDGE_SCHEMA_VERSION = "1.0"
THEME_TOKEN_VERSION = "1.0"
HEALTH_SCHEMA_VERSION = "1.0"


class AddonReasonCode(StrEnum):
    """Stable host-localized reasons exposed by Add-on v2 health checks."""

    SERVICE_NOT_RUNNING = "service_not_running"
    SERVICE_UNREACHABLE = "service_unreachable"
    SETUP_INCOMPLETE = "setup_incomplete"
    WORKER_NOT_INSTALLED = "worker_not_installed"
    MODEL_NOT_INSTALLED = "model_not_installed"
    RUNTIME_INCOMPATIBLE = "runtime_incompatible"
    CONTRACT_INCOMPATIBLE = "contract_incompatible"
    CAPABILITY_NOT_GRANTED = "capability_not_granted"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    HEALTH_CHECK_FAILED = "health_check_failed"
    UNKNOWN = "unknown"


class AddonHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    SETUP_REQUIRED = "setup_required"


class ContributionAvailability(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
