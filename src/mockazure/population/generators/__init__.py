"""Versioned curated generators: no dynamic imports or executable profile hooks."""

from mockazure.population.generators import cloud, directory, evidence, mail, recipes, workloads

VERSION = "lab-generators-v2"
GENERATORS = {
    "directory.users": (directory, "directory.users"),
    "directory.groups": (directory, "directory.groups"),
    "evidence.signins": (evidence, "evidence.signIn"),
    "evidence.suspicious-signins": (evidence, "evidence.signIn"),
    "evidence.directory-audits": (evidence, "evidence.directoryAudit"),
    "security.alerts": (evidence, "security.alerts_v2"),
    "activity.records": (evidence, "activity.records"),
    "mail.messages": (mail, "mail.messages"),
    "workloads.sites": (workloads, "sharepoint.site"),
    "workloads.drives": (workloads, "sharepoint.drive"),
    "workloads.teams": (workloads, "teams.team"),
    "workloads.channels": (workloads, "teams.channel"),
    "workloads.devices": (workloads, "intune.managedDevice"),
    "cloud.inventory": (cloud, "arm.inventory"),
    "cloud.logs": (cloud, "monitor.records"),
}


GENERATORS.update(
    {
        "recipe." + name: (recipes, family)
        for name, (family, _, _) in recipes.RECIPE_FAMILIES.items()
    }
)


def inventory():
    return {
        name: {"family": family, "version": VERSION, "max_batch": 1000}
        for name, (_, family) in GENERATORS.items()
    }


def generate(name, parameters, refs, *, offset, limit):
    if name not in GENERATORS:
        raise ValueError("Unknown packaged generator")
    module, _ = GENERATORS[name]
    return module.generate(name, parameters, refs, offset=offset, limit=limit)
