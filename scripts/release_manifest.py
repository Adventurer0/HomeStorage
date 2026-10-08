"""Validate a tagged source version and describe verified Docker-save archives."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path


MAX_ARCHIVE_SIZE = 1024 * 1024 * 1024


def validate_source(version: str, revision: str, source: Path) -> None:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("Release version must be MAJOR.MINOR.PATCH without leading zeroes")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Release revision must be the complete lowercase 40-character Git SHA")
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    constants = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets
        ):
            constants.append(ast.literal_eval(node.value))
    if constants != [version]:
        raise ValueError("Release tag does not match the source VERSION in app/version.py")


def create_manifest(version: str, revision: str, directory: Path) -> dict:
    images = {}
    for arch in ("amd64", "arm64"):
        name = f"homestorage-{version}-linux-{arch}.tar.gz"
        asset = directory / name
        if asset.is_symlink() or not asset.is_file():
            raise ValueError(f"Missing regular release archive: {name}")
        size = asset.stat().st_size
        if not 0 < size <= MAX_ARCHIVE_SIZE:
            raise ValueError(f"Invalid archive size: {name}")
        checksum = hashlib.sha256()
        with asset.open("rb") as stream:
            if stream.read(2) != b"\x1f\x8b":
                raise ValueError(f"Archive is not gzip: {name}")
            stream.seek(0)
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        images[arch] = {
            "image": f"homestorage:{version}-{arch}",
            "asset": name,
            "sha256": checksum.hexdigest(),
            "size": size,
        }
    return {"schema_version": 1, "version": version, "revision": revision, "images": images}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source", type=Path, default=Path("app/version.py"))
    parser.add_argument("--asset-dir", type=Path, default=Path("release-assets"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-source-only", action="store_true")
    args = parser.parse_args()
    try:
        validate_source(args.version, args.revision, args.source)
        if args.check_source_only:
            print(f"Validated source version {args.version}")
            return
        manifest = create_manifest(args.version, args.revision, args.asset_dir)
        output = args.output or args.asset_dir / "homestorage-update.json"
        output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Created {output.name} with verified amd64 and arm64 checksums")
    except (ValueError, OSError, SyntaxError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
