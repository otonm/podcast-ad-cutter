# Technology Stack

**Analysis Date:** 2026-03-28

## Languages

**Primary:**
- Python 3.12+ - All application logic, CLI, and pipeline components

## Runtime

**Environment:**
- Python 3.12 (minimum: `requires-python = ">=3.12"`)

**Package Manager:**
- `uv` - Fast Python package installer and runner
- Lockfile: present (managed by uv)

## Frameworks

**Core:**
- `pydantic` (>=2) - Data validation and model definitions for configuration and runtime data
- `pydantic-settings` (>=2) - Environment variable and configuration management

**HTTP Client:**
- `aiohttp` (>=3) - Async HTTP client for downloading podcast feeds and episodes

**Database:**
- `aiosqlite` (>=0.22.1) - Async SQLite interface for podcast metadata, transcriptions, and costs

**LLM Integration:**
- `litellm` (>=1.82.6) - Unified LLM client supporting multiple providers (Groq, OpenAI, OpenRouter)

**Configuration:**
- `pyyaml` (>=6) - YAML parsing for config files
- `python-dotenv` (>=1) - Environment variable loading from `.env` files

**Utilities:**
- `python-slugify` (>=8) - URL-safe slug generation for filenames

**Testing:**
- `pytest` (>=9.0.2) - Test runner
- `pytest-asyncio` (>=0.24) - Async test support
- `pytest-cov` (>=7.0.0) - Coverage reporting
- `aioresponses` (>=0.7) - Mock HTTP responses in async tests

**Development:**
- `ruff` (>=0.15.7) - Fast Python linter and formatter (all-in-one)
- `mypy` (>=1.19.1) - Static type checker with strict mode enabled
- `types-pyyaml` (>=6.0.12.20250915) - Type stubs for pyyaml

## Key Dependencies

**Critical:**
- `litellm` - Abstracts multiple LLM providers (Groq, OpenAI, OpenRouter) for transcription, topic extraction, and ad detection
- `aiohttp` - Powers async HTTP requests for podcast feed and episode downloads
- `aiosqlite` - Async SQLite for tracking episodes, transcriptions, costs, and metadata
- `pydantic` - Runtime validation of configuration and all data models ensures type safety

**Infrastructure:**
- `ffmpeg` (external binary, not pip) - Audio processing (probing, preprocessing, conversion)

## Configuration

**Environment:**
- Loaded from `.env` file via `python-dotenv`
- Supports `GROQ_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` environment variables
- Reference: `.env.example` documents required variables

**Build:**
- `pyproject.toml` - Single source of truth for project metadata, dependencies, and tool configuration
  - Ruff config: 120-char line length, Python 3.12 target, strict linting rules
  - MyPy config: strict mode, pydantic plugin, Python 3.12 target
  - Pytest config: async mode auto, test paths in `tests/`, omit coverage for `example_cost_calculation.py`

## Platform Requirements

**Development:**
- Python 3.12+
- `ffmpeg` binary accessible in PATH (for audio processing)
- Terminal for CLI interface

**Production:**
- Python 3.12+
- `ffmpeg` binary accessible in PATH
- SQLite support (included in Python standard library)
- Network access for LLM API calls (Groq, OpenAI, or OpenRouter)
- Sufficient disk space for audio caching and output directories

---

*Stack analysis: 2026-03-28*
