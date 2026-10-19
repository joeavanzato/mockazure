"""Build pinned original SDK runners outside the source tree."""

import argparse
import shutil
import subprocess
from pathlib import Path

from tools.prepare_clients import prepare as prepare_modules

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    prepare_modules(ROOT / "integrations/manifest.json", ROOT / "work/vendor", verify_only=True)
    output = ROOT / "work/cloud-b-clients"
    npm = output / "npm"
    npm.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json", "tsconfig.json", "runner.ts"):
        shutil.copyfile(ROOT / "integrations/typescript/cloud-workflows" / name, npm / name)
    commands = [
        ([shutil.which("npm") or "npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], npm),
        (
            [
                "node",
                str(npm / "node_modules/typescript/bin/tsc"),
                "--project",
                str(npm / "tsconfig.json"),
            ],
            ROOT,
        ),
    ]
    project = ROOT / "integrations/dotnet/CloudWorkflows/CloudWorkflows.csproj"
    intermediate = "-p:BaseIntermediateOutputPath=" + (output / "dotnet-obj").as_posix() + "/"
    commands.extend(
        [
            (
                [
                    "dotnet",
                    "restore",
                    str(project),
                    "--locked-mode",
                    "--packages",
                    str(output / "nuget"),
                    intermediate,
                ],
                ROOT,
            ),
            (
                [
                    "dotnet",
                    "build",
                    str(project),
                    "--no-restore",
                    intermediate,
                    "-o",
                    str(output / "dotnet"),
                ],
                ROOT,
            ),
        ]
    )
    for command, cwd in commands:
        subprocess.run(command, cwd=cwd, check=True, timeout=300)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    prepare()
