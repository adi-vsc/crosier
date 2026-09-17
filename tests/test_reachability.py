"""Every module under src/crosier must be reachable, by import, from a real
entrypoint - or be explicitly listed below as experimental. Guards against a
module being added and never wired into the shipped path.

Reachability is computed with `ast`, not by importing the code, so it holds
even for modules with side effects or missing dependencies. It cannot see
`importlib.import_module` or `__import__`; a module wired in only that way has
to be listed in EXPERIMENTAL."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "crosier"

ENTRYPOINTS = [
    REPO_ROOT / "hooks" / "crosier_hook.py",  # the Stop hook plugin.json runs
    SRC / "cli.py",  # project.scripts: crosier = "crosier.cli:main"
]

EXCLUDED = {"__init__", "__main__"}

# Not on the shipped path today - nothing reachable from the hook or the CLI
# imports these. Verified against `ast`, not assumed.
EXPERIMENTAL = {
    "brief",  # excerpt-sizing groundwork; not called from gate/pipeline yet
    "mechanical",  # mechanical-check groundwork; not called from gate/pipeline yet
    "projection",  # projection groundwork; not called from gate/pipeline yet
}


def _crosier_names(path: Path) -> set:
    """Names of `crosier.X` (or relative `.X`) modules this file imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("crosier."):
                    names.add(alias.name.split(".")[1])
        elif isinstance(node, ast.ImportFrom):
            if node.module == "crosier":
                names.update(alias.name for alias in node.names)
            elif node.module and node.module.startswith("crosier."):
                names.add(node.module.split(".")[1])
            elif node.level > 0:  # relative import inside the crosier package
                if node.module:
                    names.add(node.module.split(".")[0])
                else:
                    names.update(alias.name for alias in node.names)
    return names


def _reachable() -> set:
    seen = {p.stem for p in ENTRYPOINTS if p.parent == SRC}
    queue = list(ENTRYPOINTS)
    while queue:
        for name in _crosier_names(queue.pop()):
            if name not in seen:
                seen.add(name)
                module_path = SRC / f"{name}.py"
                if module_path.exists():
                    queue.append(module_path)
    return seen


def test_every_module_is_reachable_or_marked_experimental():
    all_modules = {p.stem for p in SRC.glob("*.py") if p.stem not in EXCLUDED}
    unaccounted = all_modules - _reachable() - EXPERIMENTAL
    assert not unaccounted, (
        f"{unaccounted} is not imported from any entrypoint (hooks/crosier_hook.py, "
        "cli.py) and not listed in EXPERIMENTAL - wire it in or mark it experimental"
    )


def test_experimental_modules_are_still_unreachable():
    stale = EXPERIMENTAL & _reachable()
    assert not stale, f"{stale} is now reachable - drop it from EXPERIMENTAL"
