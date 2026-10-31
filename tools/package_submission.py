from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import NamedTuple

# =============================================================================
# Module Overview
# =============================================================================
# Builds the submission copy of this repo from one git ref. `build_package`
# exports the ref with `git archive`, so ignored files such as `.env` and
# `output/` cannot leak, makes `docs/submission-readme.md` the root README,
# leaves out the files in `REMOVE` and scans the rest. `verify` builds and runs
# the Docker image the way the organizers' README says and checks the samples.

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
README_SOURCE = "docs/submission-readme.md"
IMAGE = "chameleonizer-submission"

# Every file in the ref must match `KEEP` or `REMOVE`, so a file added later
# stops the run until someone decides which list it belongs to. An entry that
# ends in "/" covers a whole directory.
KEEP = (
    # The organizers' starter files. README.md is replaced by `README_SOURCE`.
    "README.md", "RULES.md", "FAQ.md", "chameleon_design_schema_v0.9.json", "Dockerfile",
    "requirements.txt", ".gitignore", "output/.gitkeep", "brand_kit/", "input/", "renderer/", "engine/",
    # Ours: the tests, the development tooling, and the benchmark and logs behind the README's claims.
    "requirements-dev.txt", ".dockerignore", ".gitattributes", ".env.example", "tests/", "tools/", "bench/",
    "docs/benchmark.md", "docs/experiments.md", "docs/leaderboard.md", "docs/research.md",
    # The README's figures; `tools/readme_figures.py` draws them.
    "docs/assets/pipeline.svg", "docs/assets/ports.svg", "docs/assets/resize-examples.jpg",
    # The optional model harness, off by default; the kept docs link to its page.
    "docs/agentic-harness.md", "requirements-agent.txt",
    # The viewer add-on that FAQ Q5 requires but does not score. The Dockerfile copies only the
    # files and folders it names, so the add-on never reaches the scoring image.
    "addon/",
)
REMOVE = {
    ".github/README.md": "the team landing page, which GitHub shows ahead of the root README",
    "CLAUDE.md": "instructions for the team's coding agents",
    "docs/team-plan.md": "team workstreams, branch rules and the submission plan",
    README_SOURCE: "becomes the root README.md",
    "docs/assets/logo.svg": "shown only by the team landing page",
    "docs/assets/social-preview.png": "the private repo's social preview image",
    "docs/brief.pdf": "the organizers' brief, which their public starter repo does not ship",
}

# Key-shaped matches only, so prose that names a variable or a key prefix passes.
SECRETS = {
    "Anthropic API key": re.compile(rb"sk-ant-[A-Za-z0-9_-]{8,}"),
    "Anthropic credential with a value": re.compile(rb"ANTHROPIC_(?:API_KEY|AUTH_TOKEN)[ \t]*=[ \t]*[\"']?[^\s\"'#]"),
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})"),
}
# The submitted repo is public, so local paths and email addresses in a text file block it too.
# Character classes such as `folder[s]` keep these patterns from matching this file's own source.
PRIVATE = {
    "home directory path": re.compile(rb"(?:/Users|/home)/[A-Za-z0-9._-]+/"),
    "temporary directory path": re.compile(rb"/private/(?:tmp|var)/|/var/folder[s]/"),
    "email address": re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
}

FENCE = re.compile(r"^```.*?^```", re.M | re.S)
CODE_SPAN = re.compile(r"`([^`\s]+)`")
STATUS_LINE = re.compile(r"^\[(?P<task>[^\]]+)\] (?P<status>.+?) in (?P<secs>[\d.]+)s ", re.M)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Runs inside the built image, so the check uses the scoring environment's own packages and renderer.
STRUCTURAL_CHECK = """
import json
from pathlib import Path
from tools.checks import structural
results = {}
for path in sorted(Path("input").glob("*.json")):
    task = json.loads(path.read_text())
    out = json.loads((Path("output") / f"{path.stem}_output.json").read_text())
    results[path.name] = structural(task["source"], task["target_canvas"], out)
print("STRUCTURAL " + json.dumps(results))
"""


# -----------------------------------------------------------------
# Export and classify
# -----------------------------------------------------------------

def _git(*args: str) -> bytes:
    """Stdout of `git` run in this checkout; exits with git's error on failure."""
    proc = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"`git {' '.join(args)}` failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def resolve(ref: str) -> tuple[str, str]:
    """The commit sha and subject line that `ref` names."""
    proc = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"`{ref}` is not a commit in this repo; check the name or run `git fetch`")
    sha = proc.stdout.strip()
    return sha, _git("log", "-1", "--format=%s", sha).decode().strip()


def export_tree(sha: str) -> dict[str, tuple[bytes, int]]:
    """Every file in commit `sha` as path to (content, mode), read from `git archive`, never the working tree."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", sha))) as tar:
        for member in tar:
            if member.isdir():
                continue
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"{member.name}: not a plain file inside the tree; package this ref by hand")
            files[member.name] = (tar.extractfile(member).read(), member.mode & 0o777)
    return files


def classify(path: str) -> str | None:
    """`"remove"` or `"keep"` for a path in the tree, or None when neither list covers it."""
    if path in REMOVE:
        return "remove"
    if any(path == entry or (entry.endswith("/") and path.startswith(entry)) for entry in KEEP):
        return "keep"
    return None


def write_package(files: dict[str, tuple[bytes, int]], kept: list[str], dest: Path) -> None:
    """Write the `kept` files under `dest`, with `README_SOURCE` as the root README.md."""
    for path in kept + ["README.md"]:
        content, mode = files[README_SOURCE] if path == "README.md" else files[path]
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(mode)


# -----------------------------------------------------------------
# Checks on the written package
# -----------------------------------------------------------------

def _walk(pkg: Path) -> list[tuple[str, bytes]]:
    """Every file under `pkg` as (posix path relative to `pkg`, content), in path order."""
    found = []
    for folder, _dirs, names in os.walk(pkg):
        for name in names:
            path = Path(folder) / name
            found.append((path.relative_to(pkg).as_posix(), path.read_bytes()))
    return sorted(found)


def _is_text(data: bytes) -> bool:
    """Git's own heuristic: a file with no NUL byte in its first 8000 bytes is text."""
    return b"\0" not in data[:8000]


def _line(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def scan(pkg: Path) -> list[str]:
    """Findings that block the package: environment files, secrets, and private details in text files."""
    findings = []
    for path, data in _walk(pkg):
        name = path.rsplit("/", 1)[-1]
        if name != ".env.example" and (name == ".env" or name.startswith(".env.") or name.endswith(".env")):
            findings.append(f"{path}: environment file")
        patterns = {**SECRETS, **PRIVATE} if _is_text(data) else SECRETS
        for label, pattern in patterns.items():
            for match in pattern.finditer(data):
                # Never echo a secret; a private detail is shown so it can be found and fixed.
                shown = f" ({match.group().decode(errors='replace')})" if label in PRIVATE else ""
                findings.append(f"{path}:{_line(data, match.start())}: {label}{shown}")
    return findings


def readme_problems(pkg: Path) -> list[str]:
    """Paths the README names in inline code that the package does not have."""
    text = FENCE.sub("", (pkg / "README.md").read_text())
    problems = []
    for span in CODE_SPAN.findall(text):
        names_a_path = "/" in span or re.search(r"\.(md|py|json|txt|js|pdf|png|svg)$", span)
        # Templates, URLs and absolute container paths such as /app/input are not repo files.
        is_placeholder = re.search(r"[<>$*~]|://", span) or span.startswith("/")
        if names_a_path and not is_placeholder and not (pkg / span).exists():
            problems.append(f"README.md names `{span}`, which the package does not have")
    return problems


def dangling_references(pkg: Path, removed: list[str]) -> list[str]:
    """Mentions of removed files in kept text files; this script is exempt because it lists them."""
    warnings = []
    for path, data in _walk(pkg):
        if path == SELF or not _is_text(data):
            continue
        for gone in removed:
            for match in re.finditer(re.escape(gone.encode()), data):
                warnings.append(f"{path}:{_line(data, match.start())}: mentions removed {gone}")
    return warnings


# -----------------------------------------------------------------
# Output directory
# -----------------------------------------------------------------

def check_destination(out: Path, force: bool) -> None:
    """Refuse an output directory that holds anything but a previous package, before any work starts."""
    if ROOT.is_relative_to(out):
        raise SystemExit(f"{out} contains this checkout; choose a directory outside it")
    if out.exists() and not out.is_dir():
        raise SystemExit(f"{out} is a file; choose a directory")
    if not out.exists() or not any(out.iterdir()):
        return
    if not force:
        raise SystemExit(f"{out} is not empty; remove it or pass `--force` to replace a previous package")
    if (out / ".git").exists() or not (out / "Dockerfile").is_file() or not (out / "engine" / "resize.py").is_file():
        raise SystemExit(f"{out} does not look like a previous package; refusing to replace it")


def install(staging: Path, out: Path) -> None:
    """Move the checked package from `staging` to `out`, replacing what `check_destination` allowed."""
    if out.exists():
        shutil.rmtree(out)
    staging.rename(out)


# -----------------------------------------------------------------
# Docker verification
# -----------------------------------------------------------------

def _text(stream: str | bytes | None) -> str:
    # A timeout hands back bytes even when the run asked for text.
    return stream.decode(errors="replace") if isinstance(stream, bytes) else stream or ""


def _run(cmd: list[str], log: Path, timeout: int, cwd: Path | None = None) -> tuple[int | None, float, str, str]:
    """Run `cmd`, save its output to `log`, and return (exit code or None on timeout, seconds, stdout, stderr)."""
    # An inline program spans lines; the log keeps it, the console shows a placeholder. Flush so a
    # redirected log shows which step is running during a long build.
    print("$ " + " ".join("<program>" if "\n" in part else part for part in cmd), flush=True)
    start = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr = None, _text(exc.stdout), _text(exc.stderr) + f"\ntimed out after {timeout} s"
    log.write_text(f"$ {' '.join(cmd)}\n{stdout}\n--- stderr ---\n{stderr}")
    return code, time.perf_counter() - start, stdout, stderr


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join("    " + line for line in text.strip().splitlines()[-lines:])


def _is_png(path: Path) -> bool:
    return path.is_file() and path.read_bytes()[:8] == PNG_SIGNATURE


def verify(pkg: Path, no_cache: bool) -> bool:
    """Build and run the image from `pkg` as the organizers' README says; True when every sample passes."""
    if shutil.which("docker") is None:
        print("verify FAILED: `docker` is not on the PATH")
        return False
    tasks = sorted(path.name for path in (pkg / "input").glob("*.json"))
    if not tasks:
        print("verify FAILED: no sample tasks in input/")
        return False
    # A fresh output directory beside the package, so a run never writes into the package itself.
    work = Path(tempfile.mkdtemp(prefix=f"{pkg.name}-verify-", dir=pkg.parent))
    outputs = work / "output"
    outputs.mkdir()

    # The organizers' README build command; `--no-cache` is opt-in for a cold build.
    build = ["docker", "build", "--platform", "linux/amd64", *(["--no-cache"] if no_cache else []), "-t", IMAGE, "."]
    code, secs, stdout, stderr = _run(build, work / "build.log", timeout=3600, cwd=pkg)
    print(f"build: {'ok' if code == 0 else 'FAILED'} in {secs:.1f} s, log {work / 'build.log'}")
    if code != 0:
        print(_tail(stdout + stderr))
        return False
    size = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{.Size}}"],
                          capture_output=True, text=True).stdout.strip()
    if size.isdigit():
        print(f"image: {IMAGE}, {int(size) / 1e6:.0f} MB")

    # The organizers' README run command: no network, input/ and output/ mounted.
    run = ["docker", "run", "--rm", "--network", "none",
           "-v", f"{pkg / 'input'}:/app/input", "-v", f"{outputs}:/app/output", IMAGE]
    code, secs, stdout, stderr = _run(run, work / "run.log", timeout=1800)
    print(f"run: {'ok' if code == 0 else 'FAILED'} in {secs:.1f} s, outputs in {outputs}")
    if code != 0:
        print(_tail(stdout + stderr))
    statuses = {m["task"]: (m["status"], float(m["secs"])) for m in STATUS_LINE.finditer(stdout)}

    # Hard rules via the package's own `tools/checks.py`, mounted into the image it was built for.
    check = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
             "-v", f"{pkg / 'tools'}:/app/tools:ro", "-v", f"{pkg / 'input'}:/app/input:ro",
             "-v", f"{outputs}:/app/output:ro", IMAGE, "-c", STRUCTURAL_CHECK]
    check_code, secs, check_out, check_err = _run(check, work / "check.log", timeout=900)
    report = next((line for line in check_out.splitlines() if line.startswith("STRUCTURAL ")), None)
    checked = check_code == 0 and report is not None
    problems = json.loads(report[len("STRUCTURAL "):]) if checked else {}
    print(f"hard-rule check: {'ok' if checked else 'FAILED'} in {secs:.1f} s, log {work / 'check.log'}")
    if not checked:
        print(_tail(check_out + check_err))

    passed = code == 0 and checked
    for task in tasks:
        status, task_secs = statuses.get(task, ("no status line", 0.0))
        stem = task.removesuffix(".json")
        written = (outputs / f"{stem}_output.json").is_file() and _is_png(outputs / f"{stem}_output.png")
        violations = problems.get(task, ["not checked"])
        ok = status == "OK" and written and not violations
        passed = passed and ok
        detail = "hard rules pass" if not violations else "; ".join(violations)
        print(f"  {'PASS' if ok else 'FAIL'}  {task}: {status} in {task_secs:.2f} s, "
              f"{'JSON and PNG written' if written else 'output files missing'}, {detail}")
    print(f"verify: {'PASS' if passed else 'FAILED'}")
    return passed


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------

class Package(NamedTuple):
    """What `build_package` wrote: the commit it came from, the files it left out, and notes for a reader."""
    sha: str
    subject: str
    removed: list[str]
    warnings: list[str]


def build_package(ref: str, out: Path, force: bool) -> Package:
    """Write the checked package for `ref` to `out`."""
    check_destination(out, force)
    sha, subject = resolve(ref)
    files = export_tree(sha)
    unclassified = sorted(path for path in files if classify(path) is None)
    if unclassified:
        raise SystemExit(f"these files are in neither `KEEP` nor `REMOVE` in {SELF}; add each to one:\n  "
                         + "\n  ".join(unclassified))
    if README_SOURCE not in files:
        raise SystemExit(f"`{ref}` has no `{README_SOURCE}` to use as README.md")
    kept = sorted(path for path in files if classify(path) == "keep" and path != "README.md")
    removed = sorted(path for path in files if classify(path) == "remove")

    out.parent.mkdir(parents=True, exist_ok=True)
    # Build beside `out` and move into place only after every check passes, so `out` is never half-made.
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.partial-", dir=out.parent))
    # mkdtemp makes the directory private to its owner; the package root should open like its subfolders.
    staging.chmod(0o755)
    try:
        write_package(files, kept, staging)
        blockers = scan(staging) + readme_problems(staging)
        if blockers:
            raise SystemExit("package blocked, nothing written:\n  " + "\n  ".join(blockers))
        warnings = dangling_references(staging, removed)
        install(staging, out)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return Package(sha, subject, removed, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the submission copy of one git ref, without publishing it.")
    parser.add_argument("ref", help="commit, branch or tag to package, such as origin/exp/engine-v2")
    parser.add_argument("out", type=Path, help="directory for the package; it must be new, empty or a previous package")
    parser.add_argument("--verify", action="store_true",
                        help="also build and run the image as the organizers' README says")
    parser.add_argument("--no-cache", action="store_true", help="with --verify, build without Docker's layer cache")
    parser.add_argument("--force", action="store_true", help="replace a previous package at OUT")
    parser.add_argument("--list", action="store_true", help="print every kept file with its size")
    args = parser.parse_args()
    out = args.out.expanduser().resolve()

    start = time.perf_counter()
    package = build_package(args.ref, out, args.force)
    kept = _walk(out)
    print(f"package: {out}")
    print(f"ref: {args.ref} = {package.sha} ({package.subject})")
    print(f"kept: {len(kept)} files, {sum(len(data) for _, data in kept):,} bytes; "
          f"README.md is {README_SOURCE} from this ref")
    if args.list:
        for path, data in kept:
            print(f"  {len(data):>9,}  {path}")
    print(f"removed: {len(package.removed)} files")
    for path in package.removed:
        print(f"  {path}: {REMOVE[path]}")
    absent = sorted(set(REMOVE) - set(package.removed))
    if absent:
        print(f"listed in `REMOVE` but not in this ref: {', '.join(absent)}")
    print("scan: no environment files, secrets, local paths or email addresses")
    print(f"warnings: {len(package.warnings)}")
    for warning in package.warnings:
        print(f"  {warning}")
    print(f"packaged in {time.perf_counter() - start:.1f} s")
    if args.verify and not verify(out, args.no_cache):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
