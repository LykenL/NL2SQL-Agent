#!/usr/bin/env python3
"""Check parity between the pip requirements and PEP 621 dependencies."""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^]]+\])?\s*(.*)$")


def parse_requirement(text: str, source: str, line_number: int) -> tuple[str, str] | None:
    text = text.split("#", 1)[0].strip().split(";", 1)[0].strip()
    if not text:
        return None
    match = REQ_RE.match(text)
    if not match:
        raise ValueError(f"{source}:{line_number}: cannot parse requirement: {text}")
    name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
    specifier = re.sub(r"\s+", "", match.group(2))
    return name, specifier


def from_requirements(path: Path) -> set[tuple[str, str]]:
    result = set()
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parsed = parse_requirement(line, path.name, number)
        if parsed:
            result.add(parsed)
    return result


def from_project(values: list[str], source: str) -> set[tuple[str, str]]:
    result = set()
    for number, value in enumerate(values, 1):
        parsed = parse_requirement(value, source, number)
        if parsed:
            result.add(parsed)
    return result


def display(items: set[tuple[str, str]]) -> str:
    return ", ".join(f"{name}{specifier}" for name, specifier in sorted(items))


def main() -> int:
    try:
        with (ROOT / "pyproject.toml").open("rb") as manifest:
            project = tomllib.load(manifest)["project"]
        backend = from_requirements(ROOT / "backend-requirements.txt")
        pipeline_file = from_requirements(ROOT / "pipelines-requirements.txt")
        dependencies = from_project(project.get("dependencies", []), "pyproject.toml dependencies")
        extras = from_project(
            project.get("optional-dependencies", {}).get("pipelines", []),
            "pyproject.toml pipelines extra",
        )
    except (OSError, KeyError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"Dependency parity check could not parse inputs: {error}", file=sys.stderr)
        return 1

    problems = []
    backend_only = backend - dependencies
    project_only = dependencies - backend
    if backend_only:
        problems.append(f"Only in backend-requirements.txt: {display(backend_only)}")
    if project_only:
        problems.append(f"Only in pyproject.toml [project].dependencies: {display(project_only)}")

    allowed_pipeline = dependencies | extras
    undeclared_pipeline = pipeline_file - allowed_pipeline
    if undeclared_pipeline:
        problems.append(
            "Pipelines requirements not declared with an identical specifier in dependencies or "
            f"the pipelines extra: {display(undeclared_pipeline)}"
        )
    missing_pipeline = {item for item in extras if item[0] not in {name for name, _ in pipeline_file}}
    if missing_pipeline:
        problems.append(
            "Only in pyproject.toml [project.optional-dependencies].pipelines: "
            f"{display(missing_pipeline)}"
        )

    if problems:
        print("Dependency parity check failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(
        "Dependency parity OK: "
        f"backend={len(backend)}, pipelines-requirements={len(pipeline_file)}, pipelines-extra={len(extras)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
