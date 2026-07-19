from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from scripts import build_release


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> None:
    process = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert process.returncode == 0, process.stderr


@pytest.fixture(scope="module")
def release_repo(tmp_path_factory) -> Path:
    parent = tmp_path_factory.mktemp("release-repo")
    repo = parent / "repo"
    shutil.copytree(
        ROOT,
        repo,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            "__pycache__",
            "*.egg-info",
            "*.pyc",
            "artifacts",
            "build",
            "dist",
        ),
    )
    run(["git", "init", "-q", "-b", "main"], repo)
    run(["git", "config", "user.name", "Release Test"], repo)
    run(["git", "config", "user.email", "release-test@example.invalid"], repo)
    run(["git", "add", "."], repo)
    run(["git", "commit", "-q", "-m", "release fixture"], repo)
    return repo


def fake_spec(payload: bytes) -> build_release.DependencyWheel:
    return build_release.DependencyWheel(
        filename="pyyaml-6.0.3-cp311-cp311-macosx_11_0_arm64.whl",
        url=(
            "https://files.pythonhosted.org/packages/test/"
            "pyyaml-6.0.3-cp311-cp311-macosx_11_0_arm64.whl"
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        python="cp311",
        architecture="arm64",
    )


def test_dirty_tree_is_refused_before_release_work(release_repo, tmp_path):
    dirty = release_repo / "dirty.txt"
    dirty.write_text("not committed\n")
    payload = b"fake dependency wheel"
    spec = fake_spec(payload)
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    (wheel_dir / spec.filename).write_bytes(payload)

    with pytest.raises(build_release.ReleaseError, match="dirty worktree"):
        build_release.build_release(
            repo=release_repo,
            output_dir=tmp_path / "out",
            dependency_wheel_dir=wheel_dir,
            python=Path(sys.executable),
            dependency_specs=(spec,),
            validate_full_matrix=False,
        )

    assert not (tmp_path / "out").exists()
    dirty.unlink()


def test_locked_matrix_and_hash_fail_closed(tmp_path):
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    destination = tmp_path / "collected"

    with pytest.raises(build_release.ReleaseError, match="missing"):
        build_release._collect_dependency_wheels(
            destination,
            provided=wheel_dir,
        )

    first = build_release.PYYAML_WHEELS[0]
    (wheel_dir / first.filename).write_bytes(b"wrong wheel")
    with pytest.raises(build_release.ReleaseError, match="SHA256 mismatch"):
        build_release._collect_dependency_wheels(
            tmp_path / "collected-again",
            provided=wheel_dir,
        )

    incomplete = tuple(build_release.PYYAML_WHEELS[:-1])
    with pytest.raises(build_release.ReleaseError, match="incomplete"):
        build_release._collect_dependency_wheels(
            tmp_path / "incomplete",
            provided=wheel_dir,
            specs=incomplete,
        )


def archive_members(artifact: Path) -> tuple[str, set[str], dict[str, bytes]]:
    with tarfile.open(artifact, mode="r:gz") as archive:
        files = {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile()
        }
    roots = {PurePosixPath(name).parts[0] for name in files}
    assert len(roots) == 1
    root = roots.pop()
    relative = {
        PurePosixPath(name).relative_to(root).as_posix()
        for name in files
    }
    return root, relative, files


def test_release_archive_is_deterministic_allowlisted_and_runtime_free(
    release_repo,
    tmp_path,
):
    payload = b"test-only dependency wheel bytes\n"
    spec = fake_spec(payload)
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    (wheel_dir / spec.filename).write_bytes(payload)

    first = build_release.build_release(
        repo=release_repo,
        output_dir=tmp_path / "first",
        dependency_wheel_dir=wheel_dir,
        python=Path(sys.executable),
        dependency_specs=(spec,),
        validate_full_matrix=False,
    )
    second = build_release.build_release(
        repo=release_repo,
        output_dir=tmp_path / "second",
        dependency_wheel_dir=wheel_dir,
        python=Path(sys.executable),
        dependency_specs=(spec,),
        validate_full_matrix=False,
    )

    assert first.sha256 == second.sha256
    assert first.artifact.read_bytes() == second.artifact.read_bytes()
    assert first.outer_checksums.read_text() == (
        f"{first.sha256}  {first.artifact.name}\n"
    )

    root, relative, files = archive_members(first.artifact)
    assert root == "roundtable-messaging-0.1.0"
    assert {
        "BUILD-METADATA.json",
        "CREDITS.md",
        "LICENSE",
        "NOTICE",
        "PROVENANCE.md",
        "README.md",
        "SHA256SUMS",
        "docs/architecture.md",
        "docs/compatibility.md",
        "docs/install.md",
        "docs/provenance/source-commits.tsv",
        "docs/release.md",
        "install",
        "roundtable_packaging/__init__.py",
        "roundtable_packaging/cli.py",
        "scripts/install.sh",
        "scripts/uninstall.sh",
        "uninstall",
    }.issubset(relative)
    assert any(
        name.startswith("wheels/roundtable_messaging-0.1.0-")
        and name.endswith("-py3-none-any.whl")
        for name in relative
    )
    assert f"wheels/{spec.filename}" in relative

    forbidden = build_release.FORBIDDEN_COMPONENTS
    for name in relative:
        assert not forbidden.intersection(PurePosixPath(name).parts)
        assert not name.endswith(".pyc")

    metadata_name = f"{root}/BUILD-METADATA.json"
    metadata = json.loads(files[metadata_name])
    assert metadata["source_commit"] == first.commit
    assert metadata["version"] == "0.1.0"
    assert metadata["project_wheel"]["tag"] == "py3-none-any"
    toolchain = metadata["deterministic_build"]["toolchain"]
    assert toolchain["implementation"]
    assert toolchain["python"]
    assert toolchain["pip"]
    assert toolchain["setuptools"]
    assert toolchain["wheel"]

    sums_name = f"{root}/SHA256SUMS"
    checksums = files[sums_name].decode().splitlines()
    checked = {}
    for line in checksums:
        expected, name = line.split("  ", 1)
        checked[name] = expected
        assert hashlib.sha256(files[f"{root}/{name}"]).hexdigest() == expected
    assert "SHA256SUMS" not in checked
    assert set(checked) == relative - {"SHA256SUMS"}
