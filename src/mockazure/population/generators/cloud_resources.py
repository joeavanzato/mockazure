"""Typed management workflow bodies; all IDs belong to the supplied local tenant."""


def resources(group, tenant, label):
    rows = []

    def add(typ, name, body, version):
        namespace, kind = typ.split("/", 1)
        identifier = group + "/providers/" + namespace + "/" + kind + "/" + name
        rows.append({"id": identifier, "type": typ, "version": version, "body": body})
        return identifier

    network = "2025-03-01"
    compute = "2025-04-01"
    vnet = add(
        "Microsoft.Network/virtualNetworks",
        "net-" + label,
        {
            "location": "eastus",
            "properties": {"addressSpace": {"addressPrefixes": ["10.20.0.0/16"]}},
        },
        network,
    )
    nsg = add(
        "Microsoft.Network/networkSecurityGroups", "nsg-" + label, {"location": "eastus"}, network
    )

    def child(parent, typ, segment, name, body, version):
        identifier = parent + "/" + segment + "/" + name
        rows.append({"id": identifier, "type": typ, "version": version, "body": body})
        return identifier

    child(
        nsg,
        "Microsoft.Network/networkSecurityGroups/securityRules",
        "securityRules",
        "https",
        {
            "properties": {
                "priority": 100,
                "direction": "Inbound",
                "access": "Allow",
                "protocol": "Tcp",
                "sourcePortRange": "*",
                "destinationPortRange": "443",
                "sourceAddressPrefix": "*",
                "destinationAddressPrefix": "*",
            }
        },
        network,
    )
    subnet = child(
        vnet,
        "Microsoft.Network/virtualNetworks/subnets",
        "subnets",
        "default",
        {"properties": {"addressPrefix": "10.20.1.0/24", "networkSecurityGroup": {"id": nsg}}},
        network,
    )
    public = add(
        "Microsoft.Network/publicIPAddresses",
        "ip-" + label,
        {
            "location": "eastus",
            "sku": {"name": "Standard"},
            "properties": {"publicIPAllocationMethod": "Static"},
        },
        network,
    )
    nic = add(
        "Microsoft.Network/networkInterfaces",
        "nic-" + label,
        {
            "location": "eastus",
            "properties": {
                "ipConfigurations": [
                    {
                        "name": "primary",
                        "properties": {"subnet": {"id": subnet}, "publicIPAddress": {"id": public}},
                    }
                ]
            },
        },
        network,
    )
    disk = add(
        "Microsoft.Compute/disks",
        "disk-" + label,
        {
            "location": "eastus",
            "sku": {"name": "Standard_LRS"},
            "properties": {
                "creationData": {"createOption": "Empty"},
                "diskSizeGB": 32,
                "osType": "Linux",
            },
        },
        "2025-01-02",
    )
    vm = add(
        "Microsoft.Compute/virtualMachines",
        "vm-" + label,
        {
            "location": "eastus",
            "properties": {
                "hardwareProfile": {"vmSize": "Standard_B1s"},
                "networkProfile": {"networkInterfaces": [{"id": nic}]},
                "storageProfile": {
                    "osDisk": {
                        "createOption": "Attach",
                        "osType": "Linux",
                        "managedDisk": {"id": disk},
                    }
                },
            },
        },
        compute,
    )
    child(
        vm,
        "Microsoft.Compute/virtualMachines/extensions",
        "extensions",
        "agent",
        {
            "location": "eastus",
            "properties": {
                "publisher": "MockAzure.Local",
                "type": "InventoryOnly",
                "typeHandlerVersion": "1.0",
                "settings": {"label": label},
            },
        },
        compute,
    )
    add(
        "Microsoft.KeyVault/vaults",
        "vault-" + label,
        {
            "location": "eastus",
            "properties": {
                "tenantId": tenant,
                "sku": {"family": "A", "name": "standard"},
                "enableRbacAuthorization": True,
            },
        },
        "2025-05-01",
    )
    add(
        "Microsoft.OperationalInsights/workspaces",
        "logs-" + label,
        {"location": "eastus", "properties": {"sku": {"name": "PerGB2018"}, "retentionInDays": 30}},
        "2025-02-01",
    )
    add(
        "Microsoft.Storage/storageAccounts",
        "store" + label,
        {
            "location": "eastus",
            "kind": "StorageV2",
            "sku": {"name": "Standard_LRS"},
            "properties": {},
        },
        "2025-06-01",
    )
    return rows
