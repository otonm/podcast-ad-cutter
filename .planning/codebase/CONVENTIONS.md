# Code Conventions

## Language & Target

- Python 3.12, `from __future__ import annotations` in every module
- `target-version = "py312"` in ruff config
- `line-length = 120`

## Async Patterns

- Fully async throughout — no synchronous I/O in production code
- All public component methods are `async def`
- `async with` for all resource lifetimes (DB connections, HTTP sessions)
- `asyncio.gather` used for concurrent feed downloads
- Sync filesystem operations (e.g., `Path.glob`, `Path.is_dir`) inside async functions are suppressed with `# noqa: ASYNC240` — a known trade-off, not an oversight

## Class Structure

- One public class per module with a clear single responsibility
- `Pipeline` is the sole owner of `Config` — no component below it imports from the config module
- Components receive plain typed values in their constructors; they do not accept config objects
- Private helpers prefixed with `_` (both methods and module-level sentinels)
- `@dataclass(slots=True)` used for internal data-grouping structs (e.g., `_Stores`)
- `TYPE_CHECKING` guard used to avoid circular imports for type-only references

## Naming Conventions

- `snake_case` for everything (variables, functions, methods, modules)
- `PascalCase` for classes and Pydantic models
- `UPPER_SNAKE_CASE` for module-level constants and prompt templates
- Private module-level helpers: `_underscore_prefix`
- Store classes follow `<Entity>Store` pattern (e.g., `EpisodeStore`, `AdStore`)
- Component classes follow `<Action><Target>` pattern (e.g., `EpisodeDownloader`, `FeedPublisher`)

## Logging

- `logger = logging.getLogger(__name__)` at module top — never pass loggers as arguments
- **f-strings mandatory** for all log messages — `%` operator is prohibited (enforced by ruff `G004`)
- Log levels: `INFO` for pipeline milestones, `DEBUG` for detail/progress, `WARNING` for degraded-but-continuing, `ERROR`/`EXCEPTION` for failures
- Noisy third-party libraries (aiosqlite, LiteLLM) suppressed to `WARNING` in `main.py`

## Error Handling

- Custom exception hierarchy in `utils/exceptions.py` — one exception class per failure domain
- `ConfigError` raised for config validation failures at startup
- Domain exceptions (`TranscriptionError`, `AdDetectionError`, `FfmpegError`, etc.) raised by components
- `Pipeline._process_episode_until_final` catches bare `Exception` per episode to isolate failures without halting the run
- No overly broad try/except in components — exceptions propagate to the caller

## Type Annotations

- Strict mypy (`strict = true`), with `pydantic.mypy` plugin
- All function signatures annotated — parameters and return types
- `ignore_missing_imports = true` for third-party stubs
- Test files exempt from `disallow_untyped_defs`
- Pydantic `BaseModel` used for all config and domain data structures
- `pydantic-settings BaseSettings` for credential loading from env vars

## Configuration Passing

- `Config` loaded once in `main.py`, passed to `Pipeline.__init__`
- `Pipeline` extracts plain values from `Config` and passes them to each component constructor
- No component imports from `config/` — the dependency flows one way only
- `PROVIDER_KEY_MAP` in `config_loader.py` maps provider strings to credential field names

## Import Organization

- stdlib → third-party → local, separated by blank lines
- `TYPE_CHECKING` block for type-only imports to avoid runtime circular dependencies
- Inline imports inside functions used in tests to avoid circular import issues (suppressed with `PLC0415`)

## Comments

- Comments explain non-obvious constraints, workarounds, or invariants — not what the code does
- `noqa` directives always include the rule code and a short reason (e.g., `# noqa: S314 — feed XML is from trusted sources`)
- State machine guard table documented inline in `Pipeline._process_episode_until_final` as an ASCII table

## Ruff Lint

- `select = ["ALL"]` with a targeted ignore list
- Ignored rules are project-justified: `D107` (no `__init__` docstrings), `G004` (f-strings in logging already required by style), `PLR0913` (many-arg constructors), `ERA001` (commented-out code allowed), `COM812` (trailing comma conflicts with formatter)
- Per-file ignores for tests relax annotation and docstring requirements
