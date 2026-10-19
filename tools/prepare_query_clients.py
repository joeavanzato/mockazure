"""Prepare pinned upstream query workflow clients in ignored work directories."""

import argparse
import shutil
import subprocess
from pathlib import Path

from tools.prepare_clients import prepare as modules

ROOT = Path(__file__).resolve().parents[1]


def prepare(*, update_lock=False, build=True):
    modules(ROOT / "integrations/query-clients-manifest.json", ROOT / "work/cloud-f-vendor")
    output = ROOT / "work/cloud-f-clients"
    npm = output / "npm"
    npm.mkdir(parents=True, exist_ok=True)
    source = ROOT / "integrations/typescript/cloud-queries"
    for name in ("package.json", "package-lock.json", "runner.js"):
        if (source / name).exists():
            shutil.copyfile(source / name, npm / name)
    subprocess.run(
        [
            shutil.which("npm") or "npm",
            "install" if update_lock else "ci",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=npm,
        check=True,
        timeout=300,
    )
    if update_lock:
        shutil.copyfile(npm / "package-lock.json", source / "package-lock.json")
    project = ROOT / "integrations/dotnet/CloudQueries/CloudQueries.csproj"
    intermediate = "-p:BaseIntermediateOutputPath=" + (output / "dotnet-obj").as_posix() + "/"
    subprocess.run(
        [
            "dotnet",
            "restore",
            str(project),
            *(["--force-evaluate"] if update_lock else ["--locked-mode"]),
            "--packages",
            str(output / "nuget"),
            intermediate,
        ],
        cwd=ROOT,
        check=True,
        timeout=300,
    )
    if build:
        subprocess.run(
            [
                "dotnet",
                "build",
                str(project),
                "--no-restore",
                intermediate,
                "-o",
                str(output / "dotnet"),
            ],
            cwd=ROOT,
            check=True,
            timeout=300,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-lock", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    prepare(update_lock=args.update_lock, build=not args.no_build)
