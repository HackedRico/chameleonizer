from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# =============================================================================
# Module Overview
# =============================================================================
# Architecture rules for the optional model harness. The core modules stay
# pure: standard library plus `engine.core`, no file access, no environment.
# The stdlib-only adapters stay stdlib-only, no model client becomes a hard
# dependency, and the files that must match upstream stay untouched.

ROOT = Path(__file__).resolve().parents[1]
CORE = ["engine/core/agent.py", "engine/core/advice.py", "engine/core/validate.py"]
STDLIB_ADAPTERS = ["engine/adapters/openai_http.py", "engine/adapters/scripted.py"]
STDLIB = set(sys.stdlib_module_names) | {"__future__"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
    return out


@pytest.mark.parametrize("rel", CORE)
def test_core_is_pure(rel):
    path = ROOT / rel
    for name in _imports(path):
        assert name.split(".")[0] in STDLIB or name.startswith("engine.core"), (rel, name)
        banned = ("pathlib", "os", "io", "shutil", "subprocess", "socket", "urllib")
        assert name.split(".")[0] not in banned, (rel, name)
    text = path.read_text()
    for banned in ("open(", "pathlib", "os.environ"):
        assert banned not in text, (rel, banned)


@pytest.mark.parametrize("rel", STDLIB_ADAPTERS)
def test_stdlib_only_adapters(rel):
    for name in _imports(ROOT / rel):
        assert name.split(".")[0] in STDLIB or name.startswith("engine."), (rel, name)


def test_model_clients_are_not_hard_dependencies():
    # requirements.txt pins the scoring image's full freeze, which holds unrelated names such as
    # httpx2, so compare whole package names rather than substrings.
    lines = (ROOT / "requirements.txt").read_text().lower().splitlines()
    pinned = {re.split(r"[=<>~!\[; @]", line.strip(), maxsplit=1)[0].replace("_", "-")
              for line in lines if line.strip() and not line.lstrip().startswith("#")}
    for name in ("llama-cpp-python", "openai", "ollama", "httpx", "torch", "transformers"):
        assert name not in pinned, name
    assert "llama-cpp-python" in (ROOT / "requirements-agent.txt").read_text()
    for rel in ("engine/resize.py", "engine/core/pipeline.py", "engine/adapters/harness.py"):
        assert "llama_cpp" not in (ROOT / rel).read_text(), rel


def test_resize_imports_the_harness_lazily():
    tree = ast.parse((ROOT / "engine" / "resize.py").read_text())
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = [(n.module or "") for n in top if isinstance(n, ast.ImportFrom)]
    assert not any(n.startswith(("engine.adapters.harness", "engine.adapters.agent_config", "engine.core.agent"))
                   for n in names)


def test_upstream_files_are_untouched():
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git or the repository metadata is unavailable")
    result = subprocess.run([git, "diff", "--quiet", "HEAD", "--", "engine/main.py", "renderer/"], cwd=ROOT)
    assert result.returncode == 0, "engine/main.py and renderer/ must match HEAD (and upstream)"
