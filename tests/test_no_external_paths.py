"""Plumbline must run from a clean clone of this directory alone.

The library under core/ was absorbed, not vendored: nothing may reach back to
agentic-substrate, because a judge cloning only plumbline/ would not have it,
and neither does the Docker build context.
"""
import pathlib, re

ROOT = pathlib.Path(__file__).parents[1]
BANNED = re.compile(r"agentic[-_]substrate|parents\[\s*[3-9]\s*\]|\.\./\.\./")

def _sources():
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        if path == pathlib.Path(__file__):
            # This file's own docstring names "agentic-substrate" and
            # "substrate" literally, to document the invariant it enforces
            # on every *other* source file -- scanning itself would make the
            # test fail permanently on its own prose, not on a real leak.
            continue
        yield path

def test_no_source_file_reaches_outside_the_repo():
    offenders = [str(p.relative_to(ROOT)) for p in _sources()
                 if BANNED.search(p.read_text())]
    assert offenders == [], f"these reach outside plumbline/: {offenders}"

def test_the_substrate_namespace_is_gone():
    leftover = [str(p.relative_to(ROOT)) for p in _sources()
                if re.search(r"\bimport substrate\b|\bfrom substrate\b", p.read_text())]
    assert leftover == [], f"still importing the old namespace: {leftover}"
