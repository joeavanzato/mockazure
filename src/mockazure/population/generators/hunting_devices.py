"""Synthetic host observations; no command, process or network operation is executed."""

import json


def row(g, table, i):
    person = g.person(i)
    common = {"DeviceId": g.device_id(i), "DeviceName": g.device_name(i), "ReportId": i + 1}
    if table == "DeviceInfo":
        return common | {
            "AadDeviceId": g.id("device", i % g.users),
            "OSPlatform": "Windows10",
            "OSBuild": 19045,
            "OSVersion": "10.0.19045",
            "IsAzureADJoined": True,
            "DeviceType": "Workstation",
            "MachineGroup": "Business",
            "LoggedOnUsers": json.dumps([{"UserName": person["userPrincipalName"]}]),
            "AdditionalFields": json.dumps({"simulation": "fixture inventory"}),
        }
    if table == "DeviceNetworkInfo":
        return common | {
            "NetworkAdapterName": "Synthetic Ethernet",
            "NetworkAdapterStatus": "Up",
            "NetworkAdapterType": "Ethernet",
            "IPAddresses": json.dumps(
                [{"IPAddress": f"192.0.2.{i % 250 + 1}", "SubnetPrefix": 24}]
            ),
            "DnsAddresses": json.dumps(["192.0.2.53"]),
        }
    common.update(
        InitiatingProcessAccountUpn=person["userPrincipalName"],
        InitiatingProcessAccountObjectId=person["id"],
        InitiatingProcessFileName="synthetic-client.exe",
    )
    if table == "DeviceLogonEvents":
        return common | {
            "ActionType": "LogonSuccess",
            "LogonType": "Interactive",
            "AccountName": person["mailNickname"],
            "AccountDomain": g.domain,
            "IsLocalAdmin": False,
            "LogonId": 1000 + i,
        }
    if table == "DeviceProcessEvents":
        return common | {
            "ActionType": "ProcessCreated",
            "FileName": "synthetic-client.exe",
            "ProcessId": 1000 + i,
            "ProcessCommandLine": "synthetic-client.exe --review",
            "AccountObjectId": person["id"],
            "AccountUpn": person["userPrincipalName"],
        }
    if table == "DeviceNetworkEvents":
        return common | {
            "ActionType": "ConnectionSuccess",
            "RemoteIP": "198.51.100.25",
            "RemotePort": 443,
            "RemoteUrl": "documents.example.test",
            "Protocol": "Tcp",
            "LocalIP": f"192.0.2.{i % 250 + 1}",
        }
    if table == "DeviceFileEvents":
        thread, reply = divmod(i % max(1, g.parameters.get("messageCopies", 32) // 2), 2)
        doc = g.rendered(thread % g.projects, reply + 1)
        return common | {
            "ActionType": "FileCreated",
            "FileName": doc.filename,
            "FolderPath": r"C:\Synthetic\Documents",
            "SHA256": doc.sha256,
            "FileSize": doc.length,
        }
    if table == "DeviceRegistryEvents":
        return common | {
            "ActionType": "RegistryValueSet",
            "RegistryKey": r"HKEY_CURRENT_USER\Software\MockAzure",
            "RegistryValueName": "Project",
            "RegistryValueType": "String",
            "RegistryValueData": str(i % g.projects + 1),
        }
    return common | {
        "ActionType": "SyntheticBusinessObservation",
        "AdditionalFields": json.dumps({"simulation": "fixture", "project": i % g.projects + 1}),
    }
