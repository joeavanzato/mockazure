import pytest

from mockazure.workloads.intune import validate_intune_record
from tests.integration.intune_support import payload


@pytest.mark.parametrize(
    "kind,derived",
    [
        (
            "deviceConfiguration",
            name + "General" + ("Configuration" if name == "windows10" else "DeviceConfiguration"),
        )
        for name in ("windows10", "ios", "android", "macOS")
    ]
    + [
        ("deviceCompliancePolicy", name + "CompliancePolicy")
        for name in ("windows10", "ios", "android", "macOS")
    ],
)
def test_pinned_platform_types(kind, derived):
    value = payload(derived, "policy")
    validate_intune_record(kind, value)
    with pytest.raises(ValueError):
        validate_intune_record(kind, {**value, "inventedSetting": True})


def test_base_or_unlisted_policy_type_is_rejected():
    with pytest.raises(ValueError):
        validate_intune_record("deviceConfiguration", {"id": "policy"})
    with pytest.raises(ValueError):
        validate_intune_record(
            "deviceConfiguration",
            {
                "id": "policy",
                "@odata.type": "#microsoft.graph.androidWorkProfileGeneralDeviceConfiguration",
            },
        )


def test_windows_scan_time_follows_pinned_zero_precision_csdl():
    value = payload("windows10GeneralConfiguration", "policy", defenderScheduledScanTime="23:59:59")
    validate_intune_record("deviceConfiguration", value)
    for bad in ("24:00:00", "12:60:00", "01:02:60", "12:00:00Z", "12:00:00.1", 3):
        with pytest.raises(ValueError):
            validate_intune_record(
                "deviceConfiguration", {**value, "defenderScheduledScanTime": bad}
            )
