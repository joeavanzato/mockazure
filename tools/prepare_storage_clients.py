"""Prepare locked, original Storage libraries outside the source tree."""

import argparse
import shutil
import subprocess
from pathlib import Path

from tools.prepare_clients import prepare as modules

ROOT = Path(__file__).resolve().parents[1]


def prepare(*, update_lock=False):
    modules(ROOT / "integrations/manifest.json", ROOT / "work/vendor")
    output = ROOT / "work/cloud-c-clients"
    npm = output / "npm"
    npm.mkdir(parents=True, exist_ok=True)
    source = ROOT / "integrations/typescript/storage-namespaces"
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
    project = ROOT / "integrations/dotnet/StorageNamespaces/StorageNamespaces.csproj"
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
    args = parser.parse_args()
    prepare(update_lock=args.update_lock)
