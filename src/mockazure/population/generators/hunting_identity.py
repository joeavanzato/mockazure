"""Synthetic identity-sensor history; never inferred from arbitrary Graph reads."""


def row(g, table, i):
    person = g.person(i)
    common = {
        "AccountObjectId": person["id"],
        "AccountUpn": person["userPrincipalName"],
        "AccountDisplayName": person["displayName"],
        "AccountName": person["mailNickname"],
        "AccountDomain": g.domain,
        "ReportId": g.id("hunting-report", i),
    }
    if table == "IdentityInfo":
        return common | {
            "IsAccountEnabled": True,
            "Department": person.get("department", "Operations"),
            "EmailAddress": person["userPrincipalName"],
            "TenantId": str(g.tenant),
            "Type": "User",
            "ChangeSource": "MockAzure fixture inventory",
        }
    common.update(
        Application="Synthetic identity sensor",
        DeviceName=g.device_name(i),
        IPAddress=f"192.0.2.{i % 250 + 1}",
        AdditionalFields={"simulation": "fixture", "sensor": "identity"},
    )
    if table == "IdentityLogonEvents":
        return common | {
            "ActionType": "LogonSuccess",
            "LogonType": "Interactive",
            "Protocol": "Kerberos",
        }
    if table == "IdentityDirectoryEvents":
        return common | {
            "ActionType": "Account updated",
            "TargetAccountUpn": person["userPrincipalName"],
            "TargetAccountDisplayName": person["displayName"],
        }
    return common | {
        "ActionType": "LDAP query",
        "QueryType": "LDAP",
        "QueryTarget": "Synthetic directory",
        "Protocol": "LDAP",
        "Query": "(objectClass=user)",
    }
