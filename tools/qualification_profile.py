"""Explicit local runtime profiles for reusing the original qualification suites."""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# stage: surface, original HTTPS, original DB, original internal network third octet
STAGES = {
    "m3": ("graph", 48443, 55435, 74),
    "m3b": ("evidence", 51443, 55436, 78),
    "m3c": ("security", 32443, 55440, 90),
    "m4": ("mail", 34443, 55442, 94),
    "m5": ("arm", 36443, 55444, 98),
}


@dataclass(frozen=True)
class QualificationProfile:
    stage: str
    release: bool = False

    @property
    def surface(self):
        return STAGES[self.stage][0]

    @property
    def index(self):
        return tuple(STAGES).index(self.stage)

    @property
    def https_port(self):
        if not self.release:
            return STAGES[self.stage][1]
        # The preserved M2 compose publishes 38443.
        return 47443 if self.stage == "m3" else 38443 + 2000 * self.index

    @property
    def database_port(self):
        return 55446 + self.index if self.release else STAGES[self.stage][2]

    @property
    def ports(self):
        return (
            self.https_port,
            self.https_port + 1,
            self.https_port + 2,
            self.https_port + 3,
            self.database_port,
        )

    @property
    def runtime_dir(self):
        return ".runtime/" + ("m6-" + self.surface if self.release else self.stage)

    @property
    def project(self):
        return "mockazure-" + ("m6-" + self.surface if self.release else self.stage)

    @property
    def image(self):
        return "mockazure:" + ("m6-" if self.release else "") + self.surface

    @property
    def compose(self):
        return "compose." + ("release." + self.surface if self.release else self.stage) + ".yaml"

    @property
    def clients_compose(self):
        return (
            "compose.clients."
            + ("release." + self.surface if self.release else self.stage)
            + ".yaml"
        )

    @property
    def internal_subnet(self):
        octet = 102 + self.index * 2 if self.release else STAGES[self.stage][3]
        return f"10.231.{octet}.0/24"

    def port(self, value):
        if not self.release:
            return value
        old = STAGES[self.stage][1]
        if old <= value <= old + 3:
            return self.https_port + value - old
        return self.database_port if value == STAGES[self.stage][2] else value

    def text(self, template):
        """Expand known source configuration templates, never arbitrary caller input."""
        if not self.release:
            return template
        old_https, old_db, old_network = STAGES[self.stage][1:]
        substitutions = {
            f"compose.clients.{self.stage}.yaml": self.clients_compose,
            f"compose.{self.stage}.yaml": self.compose,
            f"deploy/haproxy-{self.stage}.cfg": f"deploy/haproxy-release-{self.surface}.cfg",
            f".runtime/{self.stage}": self.runtime_dir,
            f"mockazure-{self.stage}": self.project,
            f"mockazure_{self.stage}": "mockazure_m6_" + self.surface,
            f"mockazure:{self.surface}": self.image,
            f"10.231.{old_network}.": f"10.231.{102 + self.index * 2}.",
            f"10.231.{old_network + 1}.": f"10.231.{103 + self.index * 2}.",
        }
        for old, new in substitutions.items():
            template = template.replace(old, new)
        for old in (*range(old_https, old_https + 4), old_db):
            if template == str(old):
                return str(self.port(old))
            # Ports occur after ':' in URLs/compose bindings. Never replace digits
            # embedded in credentials, UUIDs, unrelated timestamps or API versions.
            template = re.sub(rf"(?<=:){old}(?=[:/\"'\s,\]]|$)", str(self.port(old)), template)
        return template

    def require_local_origin(self, origin):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.port not in self.ports[:3]
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Qualification origin is not an owned local endpoint")


def profile_for(stage):
    if stage not in STAGES:
        raise ValueError("Unknown qualification stage")
    selection = os.environ.get("MOCKAZURE_QUALIFICATION_PROFILE", "")
    if selection not in {"", "release"}:
        raise ValueError("Unknown qualification profile")
    return QualificationProfile(stage, release=selection == "release")
