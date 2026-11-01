---
name: python-pro
description: Write and review Python code for meteoRegiK, including type annotations, error handling, calculations, importers, and pytest tests. Use for new modules, significant logic changes, and Python code review; follow AGENTS.md project conventions.
license: MIT
metadata:
  author: https://github.com/Jeffallan
  version: "1.1.0"
  domain: language
  triggers: Python development, type hints, async Python, pytest, mypy, dataclasses, Python best practices, Pythonic code
  role: specialist
  scope: implementation
  output-format: code
  related-skills: fastapi-expert, devops-engineer
---

# Python Pro

## meteoRegiK project conventions

These override generic recommendations here and in references: Python 3.14, uv
(not Poetry), Russian free-form comments and docstrings, Qt background workers
(no asyncio). Run the relevant pytest tests; no coverage threshold is configured.
Do not install or run mypy, black, or ruff unless separately adopted in
pyproject.toml. Follow AGENTS.md for the full project rules.

Python specialist for this Python 3.14 desktop application.

## When to Use This Skill

- Writing type-safe Python with complete type coverage
- Using Qt workers for long-running operations
- Setting up pytest test suites with fixtures and mocking
- Creating Pythonic code with comprehensions, generators, context managers
- Building packages with uv and proper project structure
- Performance optimization and profiling

## Core Workflow

1. **Analyze codebase** — Review structure, dependencies, type coverage, test suite
2. **Design interfaces** — Define protocols, dataclasses, type aliases
3. **Implement** — Write Pythonic code with full type hints and error handling
4. **Test** — Cover changed behavior and regressions with pytest; no coverage threshold.
5. **Validate** — Run relevant pytest checks; fix failures before proceeding.

## Reference Guide

Load detailed guidance based on context:

| Topic | Reference | Load When |
|-------|-----------|-----------|
| Type System | `references/type-system.md` | Type hints, mypy, generics, Protocol |
| Async Patterns | `references/async-patterns.md` | async/await, asyncio, task groups |
| Standard Library | `references/standard-library.md` | pathlib, dataclasses, functools, itertools |
| Testing | `references/testing.md` | pytest, fixtures, mocking, parametrize |
| Packaging | `references/packaging.md` | poetry, pip, pyproject.toml, distribution |

## Constraints

### MUST DO
- Type hints for all function signatures and class attributes
- Follow existing formatting and PEP 8 conventions
- Russian free-form docstrings explaining non-obvious behavior and reasons
- pytest coverage of changed behavior and regressions
- Use `X | None` instead of `Optional[X]` (Python 3.10+)
- Qt background workers for long-running operations
- Dataclasses over manual __init__ methods
- Context managers for resource handling

### MUST NOT DO
- Skip type annotations on public APIs
- Use mutable default arguments
- Mix sync and async code improperly
- Introduce unconfigured linters or formatters
- Use bare except clauses
- Hardcode secrets or configuration
- Use deprecated stdlib modules (use pathlib not os.path)

## Code Examples

### Type-annotated function with error handling
```python
from pathlib import Path

def read_config(path: Path) -> dict[str, str]:
    """Read configuration from a file.

    Args:
        path: Path to the configuration file.

    Returns:
        Parsed key-value configuration entries.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If a line cannot be parsed.
    """
    config: dict[str, str] = {}
    with path.open() as f:
        for line in f:
            key, _, value = line.partition("=")
            if not key.strip():
                raise ValueError(f"Invalid config line: {line!r}")
            config[key.strip()] = value.strip()
    return config
```

### Dataclass with validation
```python
from dataclasses import dataclass, field

@dataclass
class AppConfig:
    host: str
    port: int
    debug: bool = False
    allowed_origins: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not (1 <= self.port <= 65535):
            raise ValueError(f"Invalid port: {self.port}")
```

### Async pattern
```python
import asyncio
import httpx

async def fetch_all(urls: list[str]) -> list[bytes]:
    """Fetch multiple URLs concurrently."""
    async with httpx.AsyncClient() as client:
        tasks = [client.get(url) for url in urls]
        responses = await asyncio.gather(*tasks)
        return [r.content for r in responses]
```

### pytest fixture and parametrize
```python
import pytest
from pathlib import Path

@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.txt"
    cfg.write_text("host=localhost\nport=8080\n")
    return cfg

@pytest.mark.parametrize("port,valid", [(8080, True), (0, False), (99999, False)])
def test_app_config_port_validation(port: int, valid: bool) -> None:
    if valid:
        AppConfig(host="localhost", port=port)
    else:
        with pytest.raises(ValueError):
            AppConfig(host="localhost", port=port)
```

### mypy strict configuration (pyproject.toml)
```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

Clean `mypy --strict` output looks like:
```
Success: no issues found in 12 source files
```
Any reported error (e.g., `error: Function is missing a return type annotation`) must be resolved before the implementation is considered complete.

## Output Templates

When implementing Python features, provide:
1. Module file with complete type hints
2. Test file with pytest fixtures
3. Type checking confirmation (mypy --strict passes)
4. Brief explanation of Pythonic patterns used

## Knowledge Reference

Python 3.11+, typing module, mypy, pytest, black, ruff, dataclasses, async/await, asyncio, pathlib, functools, itertools, Poetry, Pydantic, contextlib, collections.abc, Protocol

[Documentation](https://jeffallan.github.io/claude-skills/skills/language/python-pro/)
