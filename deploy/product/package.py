#!/usr/bin/env python3
"""Build and verify a reproducible, data-free AuctionRadar application archive.

The input list is intentionally allowlist-based. Never change this to archive
the repository root or copy its complete ``config/``, ``docs/``, or ``tests/``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT = "auctionradar-product"
PRODUCT_CONFIG = (
    "source-catalog.json",
    "search-profile.json",
    "selection-feedback.json",
    "seeds.json",
)
CATALOG_SCHEMA_DEFS = (
    "sourceId",
    "assetType",
    "buyerType",
    "verificationStatus",
    "alias",
    "source",
    "verificationResource",
    "runStatus",
    "runResult",
    "runReport",
    "catalog",
)
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_FILES = 10_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PackageError(ValueError):
    """Invalid package input or archive."""


def _safe_regular_file(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PackageError(f"required package input is unavailable: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PackageError(f"package input is not a regular file: {path}")
    return path.read_bytes()


def _schema_bytes() -> bytes:
    source = ROOT / "auctionradar" / "defaults" / "source-catalog.schema.json"
    try:
        import json

        schema = json.loads(_safe_regular_file(source))
        definitions = schema["$defs"]
        schema["$defs"] = {name: definitions[name] for name in CATALOG_SCHEMA_DEFS}
        schema["$id"] = "urn:auctionradar:product:source-catalog:schema:1.0.0"
        schema["title"] = "AuctionRadar product source catalog"
        schema["description"] = (
            "Draft 2020-12 schema for portable auction-source records and run reports."
        )
        schema["$defs"]["catalog"]["properties"]["sources"]["minItems"] = 0
        # Validate the portable source contract, including catalog revisions.
        schema["$defs"]["assetType"] = {
            "enum": [
                "housing",
                "vehicles",
                "land",
                "commercial",
                "boats",
                "machinery",
                "electronics",
                "furniture",
                "collectibles",
                "other",
            ]
        }
        schema["$defs"]["source"] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "name", "url", "asset_types", "enabled", "adapter_id"],
            "properties": {
                "id": {"$ref": "#/$defs/sourceId"},
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "url": {"type": "string", "pattern": "^https://", "maxLength": 2048},
                "asset_types": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"$ref": "#/$defs/assetType"},
                },
                "enabled": {"type": "boolean"},
                "adapter_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
            },
        }
        schema["$defs"]["catalog"] = {
            "type": "object",
            "required": ["sources"],
            "properties": {
                "sources": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": 100,
                    "items": {"$ref": "#/$defs/source"},
                }
            },
        }
    except (KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
        raise PackageError("source-catalog schema cannot be transformed safely") from exc
    import json

    return (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _product_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name in (
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "README.md",
        "pyproject.toml",
        "requirements.in",
        "requirements.txt",
    ):
        files[name] = _safe_regular_file(ROOT / name)

    # Explicit package directories only: no repository-wide or recursive data copy.
    package_suffixes = {
        "auctionradar": {".py"},
        "auctionradar/connectors": {".py"},
        "auctionradar/connectors/boe": {".py"},
        "auctionradar/connectors/escogecasa": {".py"},
        "auctionradar/web": {".py"},
        "auctionradar/web/static": {".css", ".js", ".svg"},
        "auctionradar/web/templates": {".html"},
    }
    for folder, suffixes in package_suffixes.items():
        base = ROOT / folder
        if not base.is_dir() or base.is_symlink():
            raise PackageError(f"required application directory is unavailable: {folder}")
        for source in sorted(base.iterdir()):
            if source.is_dir() and not source.is_symlink():
                continue
            rel = source.relative_to(ROOT).as_posix()
            if source.suffix not in suffixes:
                continue
            if source.is_symlink() or not source.is_file():
                raise PackageError(f"unsafe application input: {rel}")
            files[rel] = _safe_regular_file(source)

    files["auctionradar/defaults/source-catalog.schema.json"] = _schema_bytes()
    product_config = ROOT / "auctionradar" / "defaults"
    for name in PRODUCT_CONFIG:
        rel = f"auctionradar/defaults/{name}"
        files[rel] = _safe_regular_file(product_config / name)

    if len(files) > MAX_FILES:
        raise PackageError("package input file count exceeds the configured limit")
    return dict(sorted(files.items()))


def _tarinfo(name: str, *, is_dir: bool = False, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if is_dir else 0o644
    info.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    info.size = 0 if is_dir else size
    return info


def build_archive(output: Path) -> str:
    output = output.expanduser().absolute()
    output = output.parent.resolve() / output.name
    try:
        output.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise PackageError("release output must be outside the source checkout")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", output.name):
        raise PackageError(
            "release filename may contain only letters, digits, dot, underscore, and hyphen"
        )
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise PackageError("release output must be a regular file or a new path")

    output.parent.mkdir(parents=True, exist_ok=True)
    files = _product_files()
    manifest = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {path}\n" for path, data in files.items()
    ).encode("utf-8")
    archive_files = {f"{ARCHIVE_ROOT}/{path}": data for path, data in sorted(files.items())}
    archive_files[f"{ARCHIVE_ROOT}/MANIFEST.sha256"] = manifest
    directory_names = {ARCHIVE_ROOT}
    for name in archive_files:
        parts = PurePosixPath(name).parts
        directory_names.update("/".join(parts[:index]) for index in range(1, len(parts)))

    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    for name in sorted(
                        directory_names, key=lambda value: (value.count("/"), value)
                    ):
                        archive.addfile(_tarinfo(name, is_dir=True))
                    for name, data in sorted(archive_files.items()):
                        archive.addfile(_tarinfo(name, size=len(data)), io.BytesIO(data))
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    digest = _sha256(output)
    sidecar = output.with_name(output.name + ".sha256")
    if sidecar.is_symlink() or (sidecar.exists() and not sidecar.is_file()):
        raise PackageError("checksum output must be a regular file or a new path")
    fd, sidecar_temp_name = tempfile.mkstemp(prefix=f".{sidecar.name}.", dir=output.parent)
    sidecar_temp = Path(sidecar_temp_name)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as stream:
            stream.write(f"{digest}  {output.name}\n")
        os.chmod(sidecar_temp, 0o644)
        os.replace(sidecar_temp, sidecar)
    except Exception:
        sidecar_temp.unlink(missing_ok=True)
        raise
    return digest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_member_name(member: tarfile.TarInfo) -> PurePosixPath:
    name = member.name
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise PackageError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise PackageError(f"unsafe archive path: {name!r}")
    if not path.parts or path.parts[0] != ARCHIVE_ROOT:
        raise PackageError(f"unexpected archive root: {name!r}")
    return path


def verify_and_extract(archive_path: Path, expected_sha256: str, destination: Path) -> Path:
    archive_path = archive_path.expanduser()
    destination = destination.expanduser()
    if not SHA256_RE.fullmatch(expected_sha256):
        raise PackageError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    if archive_path.is_symlink() or not archive_path.is_file():
        raise PackageError("archive must be a regular, non-symlink file")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PackageError("archive exceeds the compressed size limit")
    if destination.exists() or destination.is_symlink():
        raise PackageError("extraction destination already exists")
    if _sha256(archive_path) != expected_sha256:
        raise PackageError("archive SHA-256 does not match the expected digest")

    created_destination = False
    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            members = tar.getmembers()
            if not members or len(members) > MAX_FILES * 2:
                raise PackageError("archive member count is invalid")
            seen: set[str] = set()
            file_members: dict[str, tarfile.TarInfo] = {}
            total_size = 0
            for member in members:
                path = _normalized_member_name(member)
                key = path.as_posix()
                if key in seen:
                    raise PackageError(f"duplicate archive member: {key}")
                seen.add(key)
                if member.isdir():
                    continue
                if not member.isreg():
                    raise PackageError(f"archive contains a non-regular file: {key}")
                if member.size < 0 or member.size > MAX_EXPANDED_BYTES:
                    raise PackageError(f"archive member size is invalid: {key}")
                total_size += member.size
                if total_size > MAX_EXPANDED_BYTES:
                    raise PackageError("archive expanded size exceeds the configured limit")
                file_members[key] = member

            manifest_name = f"{ARCHIVE_ROOT}/MANIFEST.sha256"
            manifest_member = file_members.get(manifest_name)
            if manifest_member is None:
                raise PackageError("archive has no checksum manifest")
            stream = tar.extractfile(manifest_member)
            if stream is None:
                raise PackageError("checksum manifest cannot be read")
            manifest_bytes = stream.read(4 * 1024 * 1024 + 1)
            if len(manifest_bytes) > 4 * 1024 * 1024:
                raise PackageError("checksum manifest exceeds the configured limit")
            try:
                manifest_text = manifest_bytes.decode("ascii")
            except UnicodeDecodeError as exc:
                raise PackageError("checksum manifest is not ASCII") from exc

            expected_files: dict[str, str] = {}
            for line in manifest_text.splitlines():
                match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_./-]+)", line)
                if not match:
                    raise PackageError("checksum manifest contains a malformed entry")
                digest, relative = match.groups()
                rel_path = PurePosixPath(relative)
                if rel_path.is_absolute() or any(
                    part in {"", ".", ".."} for part in rel_path.parts
                ):
                    raise PackageError("checksum manifest contains an unsafe path")
                full_name = f"{ARCHIVE_ROOT}/{rel_path.as_posix()}"
                if full_name in expected_files:
                    raise PackageError("checksum manifest contains a duplicate path")
                expected_files[full_name] = digest
            actual_files = set(file_members) - {manifest_name}
            if set(expected_files) != actual_files:
                raise PackageError("checksum manifest does not describe the complete archive")

            verified_data: dict[str, bytes] = {}
            for name, digest in expected_files.items():
                member = file_members[name]
                content = tar.extractfile(member)
                if content is None:
                    raise PackageError(f"archive member cannot be read: {name}")
                data = content.read(member.size + 1)
                if len(data) != member.size or hashlib.sha256(data).hexdigest() != digest:
                    raise PackageError(f"archive member failed its checksum: {name}")
                verified_data[name] = data

            destination.mkdir(mode=0o700, parents=False)
            created_destination = True
            product_root = destination / ARCHIVE_ROOT
            product_root.mkdir(mode=0o755)
            for name, data in sorted(verified_data.items()):
                relative = PurePosixPath(name).relative_to(ARCHIVE_ROOT)
                target = product_root.joinpath(*relative.parts)
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(target, flags, 0o644)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
            return product_root
    except (tarfile.TarError, OSError) as exc:
        if created_destination and destination.exists():
            shutil.rmtree(destination)
        if isinstance(exc, PackageError):
            raise
        raise PackageError(f"archive could not be safely verified: {exc}") from exc
    except Exception:
        if created_destination and destination.exists():
            shutil.rmtree(destination)
        raise


def _build_command(args: argparse.Namespace) -> int:
    digest = build_archive(Path(args.output))
    print(f"sha256={digest}")
    print(f"checksum_file={Path(args.output).expanduser().absolute().name}.sha256")
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    root = verify_and_extract(Path(args.archive), args.sha256, Path(args.destination))
    print(f"verified={root}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build a reproducible product archive")
    build.add_argument("--output", required=True, help="archive path outside this checkout")
    build.set_defaults(func=_build_command)
    verify = commands.add_parser("verify", help="verify digest, manifest, and safely extract")
    verify.add_argument("--archive", required=True)
    verify.add_argument("--sha256", required=True)
    verify.add_argument("--destination", required=True)
    verify.set_defaults(func=_verify_command)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (PackageError, OSError) as exc:
        print(f"package error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
