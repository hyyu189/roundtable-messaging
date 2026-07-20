import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "bin" / "roundtable-init"


def run_init(tmp_path, *args, cwd=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "RT_PROJECTS_FILE": str(tmp_path / "projects.yaml"),
        }
    )
    return subprocess.run(
        [sys.executable, str(INIT), *args],
        cwd=cwd or tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_new_project_defaults_to_no_git(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()

    result = run_init(tmp_path, "plain", "--parent", str(parent))

    project = parent / "plain"
    assert result.returncode == 0, result.stderr
    assert (project / ".roundtable" / "agents.yaml").is_file()
    assert not (project / ".git").exists()
    assert "git: not initialized (use --git to opt in)" in result.stdout


def test_new_project_initializes_git_only_with_explicit_flag(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()

    result = run_init(tmp_path, "versioned", "--parent", str(parent), "--git")

    project = parent / "versioned"
    log = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (project / ".git").is_dir()
    assert log.returncode == 0, log.stderr
    assert log.stdout.splitlines() == [
        "Initial: versioned bootstrapped via roundtable-init"
    ]


def test_git_and_no_git_are_mutually_exclusive(tmp_path):
    result = run_init(tmp_path, "invalid", "--git", "--no-git")

    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr
    assert not (tmp_path / "invalid").exists()


def test_here_preserves_user_files_and_marked_appends_are_idempotent(tmp_path):
    project = tmp_path / "existing work"
    project.mkdir()
    originals = {
        "AGENTS.md": "# My agent rules\n\nKeep this first.\n",
        "README.md": "# My notes\n\nDo not replace me.\n",
        ".gitignore": "private-output/\n",
    }
    for rel, content in originals.items():
        (project / rel).write_text(content)

    first = run_init(tmp_path, "--here", cwd=project)
    snapshots = {
        rel: (project / rel).read_text()
        for rel in originals
    }
    second = run_init(tmp_path, "--here", cwd=project)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "configured" in first.stdout
    assert "already configured" in second.stdout
    assert (project / ".roundtable" / "agents.yaml").is_file()
    assert not (project / ".git").exists()
    for rel, original in originals.items():
        content = (project / rel).read_text()
        assert content.startswith(original)
        assert content == snapshots[rel]
        assert content.count("BEGIN Roundtable") == 1
        assert content.count("END Roundtable") == 1


def test_here_serializes_yaml_sensitive_project_path_exactly(tmp_path):
    project = tmp_path / "existing ${date} # notes"
    project.mkdir()

    result = run_init(tmp_path, "--here", cwd=project)
    document = yaml.safe_load(
        (project / ".roundtable" / "agents.yaml").read_text()
    )

    assert result.returncode == 0, result.stderr
    assert document["project"] == str(project.resolve())
    assert (project / "README.md").read_text().startswith(
        "# existing ${date} # notes\n"
    )


def test_here_recognizes_an_existing_generated_project_from_an_earlier_date(tmp_path):
    project = tmp_path / "generated"
    created = run_init(tmp_path, "generated", "--parent", str(tmp_path))
    assert created.returncode == 0, created.stderr
    readme = project / "README.md"
    original = readme.read_text()
    dated = original.replace(date.today().isoformat(), "2000-01-02")
    readme.write_text(dated)

    repeated = run_init(tmp_path, "--here", cwd=project)

    assert repeated.returncode == 0, repeated.stderr
    assert "already configured" in repeated.stdout
    assert readme.read_text() == dated
    assert "BEGIN Roundtable" not in dated


def test_here_git_flag_does_not_commit_inside_existing_repository(tmp_path):
    project = tmp_path / "repository"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=project,
        check=True,
    )
    (project / "user-file.txt").write_text("keep me uncommitted\n")

    result = run_init(tmp_path, "--here", "--git", cwd=project)
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    log = subprocess.run(
        ["git", "rev-list", "--all", "--count"],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "existing repository preserved (no init or commit)" in result.stdout
    assert status.returncode == 0, status.stderr
    assert "user-file.txt" in status.stdout
    assert ".roundtable/" in status.stdout
    assert log.returncode == 0, log.stderr
    assert log.stdout.strip() == "0"


def test_here_preflight_conflict_leaves_directory_untouched(tmp_path):
    project = tmp_path / "conflicted"
    project.mkdir()
    (project / "AGENTS.md").write_text("user-owned\n")
    (project / "README.md").mkdir()
    before = sorted(path.relative_to(project) for path in project.rglob("*"))

    result = run_init(tmp_path, "--here", cwd=project)

    after = sorted(path.relative_to(project) for path in project.rglob("*"))
    assert result.returncode != 0
    assert "expected a regular file" in result.stderr
    assert before == after
    assert (project / "AGENTS.md").read_text() == "user-owned\n"
    assert not (project / ".roundtable").exists()


def test_here_preflight_rejects_foreign_symlink_without_writes(tmp_path):
    project = tmp_path / "linked"
    project.mkdir()
    source = tmp_path / "outside-readme"
    source.write_text("outside\n")
    (project / "README.md").symlink_to(source)

    result = run_init(tmp_path, "--here", cwd=project)

    assert result.returncode != 0
    assert "refusing symbolic-link file" in result.stderr
    assert source.read_text() == "outside\n"
    assert not (project / ".roundtable").exists()


def test_here_rejects_file_at_claude_project_skills_path_without_writes(
    tmp_path,
):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    collision = project / ".claude" / "skills"
    collision.write_text("user file\n")

    result = run_init(tmp_path, "--here", cwd=project)

    assert result.returncode != 0
    assert "expected a directory" in result.stderr
    assert collision.read_text() == "user file\n"
    assert not (project / ".roundtable").exists()


def test_here_never_turns_the_home_directory_into_a_project(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    result = run_init(tmp_path, "--here", cwd=home)

    assert result.returncode != 0
    assert "refusing to use the home" in result.stderr
    assert not (home / ".roundtable").exists()
