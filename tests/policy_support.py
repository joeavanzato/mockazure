def policy_body(state="enabled", control="mfa"):
    return {
        "displayName": "Local Conditional Access",
        "state": state,
        "conditions": {
            "users": {
                "includeUsers": ["All"],
                "excludeUsers": [],
                "includeGroups": [],
                "excludeGroups": [],
            },
            "applications": {"includeApplications": ["All"], "excludeApplications": []},
            "clientAppTypes": ["all"],
        },
        "grantControls": {"operator": "OR", "builtInControls": [control]},
    }


def location_body():
    return {
        "@odata.type": "#microsoft.graph.ipNamedLocation",
        "displayName": "Local networks",
        "isTrusted": True,
        "ipRanges": [
            {"@odata.type": "#microsoft.graph.iPv4CidrRange", "cidrAddress": "127.0.0.0/8"},
            {"@odata.type": "#microsoft.graph.iPv6CidrRange", "cidrAddress": "::1/128"},
        ],
    }
