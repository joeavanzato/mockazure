"""Approved Intune structural profile and trusted observed-device contract."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

CONFIGURATIONS = frozenset(
    {
        "windows10GeneralConfiguration",
        "iosGeneralDeviceConfiguration",
        "androidGeneralDeviceConfiguration",
        "macOSGeneralDeviceConfiguration",
    }
)
POLICIES = frozenset(
    {
        "windows10CompliancePolicy",
        "iosCompliancePolicy",
        "androidCompliancePolicy",
        "macOSCompliancePolicy",
    }
)
KINDS = frozenset(
    {
        "managedDevice",
        "detectedApp",
        "deviceConfiguration",
        "deviceCompliancePolicy",
        "deviceConfigurationAssignment",
        "deviceCompliancePolicyAssignment",
        "deviceConfigurationDeviceStatus",
        "deviceComplianceDeviceStatus",
    }
)
EDGES = frozenset(
    {
        ("user", "managedDevices", "managedDevice"),
        ("detectedApp", "managedDevices", "managedDevice"),
        ("deviceConfiguration", "assignments", "deviceConfigurationAssignment"),
        ("deviceConfiguration", "deviceStatuses", "deviceConfigurationDeviceStatus"),
        ("deviceCompliancePolicy", "assignments", "deviceCompliancePolicyAssignment"),
        ("deviceCompliancePolicy", "deviceStatuses", "deviceComplianceDeviceStatus"),
    }
)
ROOTS = {
    "managedDevices": "managedDevice",
    "detectedApps": "detectedApp",
    "deviceConfigurations": "deviceConfiguration",
    "deviceCompliancePolicies": "deviceCompliancePolicy",
}
INTUNE_PLAN = "c1ec4a95-1f05-45b3-a911-aa3fa01094f5"
SCOPE = "deviceManagement"


@dataclass(frozen=True)
class DeviceObservation:
    device_id: UUID
    platform: str | None
    compliant: bool | None
    observed_at: datetime
