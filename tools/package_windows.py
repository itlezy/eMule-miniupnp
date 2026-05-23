#!/usr/bin/env python3
"""Build and package eMuleBB MiniUPnP Windows upnpc releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path


DEFAULT_VERSION = "2.2.3-emulebb.1"
PE_MACHINES = {"x64": 0x8664, "ARM64": 0xAA64}
REPO_URL = "https://github.com/emulebb/emulebb-miniupnp"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and package Windows upnpc.exe.")
    parser.add_argument("--version", default=DEFAULT_VERSION, help=f"Release version. Defaults to {DEFAULT_VERSION}.")
    parser.add_argument("--platform", required=True, choices=sorted(PE_MACHINES), help="Windows target platform.")
    parser.add_argument("--configuration", default="Release", choices=["Release"], help="MSBuild configuration.")
    parser.add_argument("--clean", action="store_true", help="Rebuild the selected target and clean staging output.")
    parser.add_argument("--output-root", type=Path, default=None, help="Directory where release artifacts are written.")
    parser.add_argument("--msbuild", type=Path, default=None, help="Optional explicit MSBuild.exe path.")
    parser.add_argument("--require-clean", action="store_true", help="Fail if the MiniUPnP git worktree is dirty.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_root = (args.output_root or repo_root / "dist" / f"miniupnpc-v{args.version}").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if args.require_clean and git_dirty(repo_root):
        raise SystemExit("MiniUPnP source tree is dirty; commit or clean it before release packaging.")

    build_upnpc(repo_root, args)
    exe_path = repo_root / "miniupnpc" / "msvc" / args.platform / args.configuration / "upnpc.exe"
    if not exe_path.is_file():
        raise SystemExit(f"MSBuild did not produce {exe_path}")
    assert_pe_machine(exe_path, args.platform)

    package = create_package(repo_root, output_root, exe_path, args)
    print(f"MiniUPnP package: {package['zip_path']}")
    print(f"MiniUPnP manifest: {package['manifest_path']}")
    print(f"MiniUPnP SHA256: {package['sha256_path']}")
    print(f"SHA256: {package['zip_sha256']}")
    return 0


def build_upnpc(repo_root: Path, args: argparse.Namespace) -> None:
    msbuild = resolve_msbuild(args.msbuild)
    project = repo_root / "miniupnpc" / "msvc" / "upnpc-static.vcxproj"
    target = "Rebuild" if args.clean else "Build"
    command = [
        str(msbuild),
        str(project),
        "/m",
        "/nologo",
        f"/t:{target}",
        f"/p:Configuration={args.configuration}",
        f"/p:Platform={args.platform}",
        "/clp:ErrorsOnly",
    ]
    subprocess.run(command, cwd=repo_root, check=True)


def resolve_msbuild(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise SystemExit(f"MSBuild.exe not found: {candidate}")
    from_path = shutil.which("MSBuild.exe") or shutil.which("msbuild")
    if from_path:
        return Path(from_path).resolve()
    vswhere = find_vswhere()
    if vswhere is not None:
        output = subprocess.check_output(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.Component.MSBuild",
                "-property",
                "installationPath",
            ],
            text=True,
        ).strip()
        if output:
            candidate = Path(output) / "MSBuild" / "Current" / "Bin" / "MSBuild.exe"
            if candidate.is_file():
                return candidate.resolve()
    for root in program_files_roots():
        vs_root = root / "Microsoft Visual Studio" / "2022"
        if vs_root.is_dir():
            for edition in sorted(vs_root.iterdir()):
                candidate = edition / "MSBuild" / "Current" / "Bin" / "MSBuild.exe"
                if candidate.is_file():
                    return candidate.resolve()
    raise SystemExit("Visual Studio 2022 MSBuild.exe was not found.")


def find_vswhere() -> Path | None:
    found = shutil.which("vswhere.exe") or shutil.which("vswhere")
    if found:
        return Path(found).resolve()
    for root in program_files_roots():
        candidate = root / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if candidate.is_file():
            return candidate.resolve()
    return None


def program_files_roots() -> tuple[Path, ...]:
    roots = []
    for name in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    return tuple(roots)


def create_package(repo_root: Path, output_root: Path, exe_path: Path, args: argparse.Namespace) -> dict[str, Path | str]:
    asset_arch = "arm64" if args.platform == "ARM64" else "x64"
    asset_name = f"emulebb-miniupnp-{args.version}-windows-{asset_arch}.zip"
    staging_root = output_root / "staging" / f"windows-{asset_arch}"
    package_root = staging_root / "MiniUPnP"
    zip_path = output_root / asset_name
    manifest_path = output_root / f"emulebb-miniupnp-{args.version}-windows-{asset_arch}.manifest.json"
    sha256_path = output_root / f"emulebb-miniupnp-{args.version}-windows-{asset_arch}.sha256.txt"

    for path in (staging_root, zip_path, manifest_path, sha256_path):
        assert_under(path, output_root)
    if args.clean and staging_root.exists():
        shutil.rmtree(staging_root)
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)

    shutil.copy2(exe_path, package_root / "upnpc.exe")
    shutil.copy2(repo_root / "LICENSE", package_root / "LICENSE-MiniUPnP.txt")
    write_readme(package_root / "README.txt", args.version, args.platform)

    manifest = build_manifest(repo_root, package_root, asset_name, args)
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (package_root / "MANIFEST.json").write_text(manifest_text, encoding="utf-8", newline="\n")
    manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")

    if zip_path.exists():
        zip_path.unlink()
    write_zip(package_root, zip_path)
    verify_zip_contents(zip_path)
    zip_sha256 = sha256(zip_path)
    sha256_path.write_text(f"{zip_sha256}  {asset_name}\n", encoding="utf-8", newline="\n")
    return {
        "zip_path": zip_path,
        "manifest_path": manifest_path,
        "sha256_path": sha256_path,
        "zip_sha256": zip_sha256,
    }


def build_manifest(repo_root: Path, package_root: Path, asset_name: str, args: argparse.Namespace) -> dict[str, object]:
    files = []
    for relative in (Path("upnpc.exe"), Path("README.txt"), Path("LICENSE-MiniUPnP.txt")):
        path = package_root / relative
        files.append({"path": relative.as_posix(), "size": path.stat().st_size, "sha256": sha256(path)})
    return {
        "schemaVersion": "emulebb-miniupnp.package.v1",
        "packageName": "emulebb-miniupnp",
        "assetName": asset_name,
        "version": args.version,
        "upstreamVersion": read_version(repo_root),
        "configuration": args.configuration,
        "platform": args.platform,
        "repository": REPO_URL,
        "sourceBranch": git_output(repo_root, "branch", "--show-current"),
        "sourceCommit": git_output(repo_root, "rev-parse", "HEAD"),
        "sourceDirty": git_dirty(repo_root),
        "builtAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files,
    }


def write_readme(path: Path, version: str, platform: str) -> None:
    text = f"""MiniUPnP upnpc for Windows
============================

Package: emulebb-miniupnp
Version: {version}
Platform: {platform}

This package contains a standalone Windows build of the MiniUPnP `upnpc`
command-line client, published by the eMuleBB project for Windows users.
MiniUPnP itself is an upstream project by Thomas Bernard and contributors.

Run `upnpc.exe -h` for command help.
"""
    path.write_text(text, encoding="utf-8", newline="\n")


def write_zip(package_root: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(package_root).as_posix()
                archive.write(path, f"MiniUPnP/{relative}")


def verify_zip_contents(zip_path: Path) -> None:
    expected = {
        "MiniUPnP/LICENSE-MiniUPnP.txt",
        "MiniUPnP/MANIFEST.json",
        "MiniUPnP/README.txt",
        "MiniUPnP/upnpc.exe",
    }
    with zipfile.ZipFile(zip_path) as archive:
        actual = set(archive.namelist())
    if actual != expected:
        raise SystemExit(f"Unexpected ZIP contents: {sorted(actual)}")


def assert_pe_machine(path: Path, platform: str) -> None:
    data = path.read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise SystemExit(f"Not a PE executable: {path}")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise SystemExit(f"Invalid PE header: {path}")
    machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
    expected = PE_MACHINES[platform]
    if machine != expected:
        raise SystemExit(f"{path} has PE machine 0x{machine:04x}, expected 0x{expected:04x} for {platform}")


def read_version(repo_root: Path) -> str:
    return (repo_root / "miniupnpc" / "VERSION").read_text(encoding="utf-8").strip()


def git_output(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=repo_root, text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        return ""


def git_dirty(repo_root: Path) -> bool:
    return bool(git_output(repo_root, "status", "--porcelain"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_under(path: Path, root: Path) -> None:
    path.resolve().relative_to(root.resolve())


if __name__ == "__main__":
    sys.exit(main())
