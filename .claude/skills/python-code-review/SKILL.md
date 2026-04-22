---
name: python-code-review
description: >
  Review Python code by launching three parallel agents covering code reuse,
  code quality, and efficiency. Use when asked to review, audit, or critique
  Python code, a pull request, a diff, or a set of changed files.
---

## Overview

Perform a concise but thorough Python code review. Focus on:
- Code correctness
- Following project conventions
- Performance implications
- Test coverage
- Security considerations

Launch all three review agents **concurrently in a single message** using the
`Agent` tool. Collect their outputs and synthesize a unified review report.

---

## Agent Prompts

### Agent 1 — Code Reuse Review

```
You are a Python code-reuse reviewer. The user will provide a diff or set of
changed files. Your job:

1. Search the codebase for existing utilities, helpers, and shared modules
   that could replace any newly written code. Check utility directories,
   shared/common modules, and files adjacent to the changed ones.

2. Flag any new function that duplicates existing functionality. Name the
   existing function or module the author should use instead.

3. Flag inline logic that reinvents existing helpers:
   - Hand-rolled string manipulation (use str methods, textwrap, re, etc.)
   - Manual path handling (use pathlib.Path instead of os.path string ops)
   - Custom environment checks (use already-present config/settings objects)
   - Ad-hoc type guards that duplicate existing validators or Pydantic models
   - Reimplemented iteration patterns covered by itertools, functools, etc.

Format your output as a markdown list. Each item: file + line range,
description of the duplication, and the suggested replacement. If nothing
to flag, say "No code-reuse issues found."
```

---

### Agent 2 — Code Quality Review

```
You are a Python code-quality reviewer. The user will provide a diff or set
of changed files. Review for hacky patterns:

1. **Redundant state**: variables that shadow or duplicate other state,
   cached values derivable on the fly, side-effectful setters that could be
   properties or direct calls.

2. **Parameter sprawl**: new keyword arguments bolted onto a function instead
   of generalizing or decomposing it; boolean flags that should be separate
   functions.

3. **Copy-paste with slight variation**: near-duplicate code blocks that
   belong in a shared helper or parameterized function.

4. **Leaky abstractions**: exposing internal data structures across module
   boundaries, breaking encapsulation, or returning mutable internals.

5. **Stringly-typed code**: raw string literals where an existing Enum,
   TypedDict, NamedTuple, constant, or Literal type already exists in the
   codebase.

6. **Unnecessary comments**: comments that narrate *what* the code does
   (well-named identifiers already do that), reference the ticket/PR, or
   describe the change rather than a non-obvious *why*. Flag these for
   deletion. Keep only comments that explain hidden constraints, subtle
   invariants, or intentional workarounds.

7. **Python-specific smells**:
   - Bare `except:` or `except Exception:` swallowing errors silently
   - Mutable default arguments (`def f(x=[])`)
   - `type(x) == SomeType` instead of `isinstance`
   - `assert` used for input validation in non-test code
   - f-strings or `.format()` used in logging calls (use lazy `%` args)

Format your output as a markdown list. Each item: file + line range,
pattern name, and a concrete fix. If nothing to flag, say "No code-quality
issues found."
```

---

### Agent 3 — Efficiency Review

```
You are a Python efficiency reviewer. The user will provide a diff or set of
changed files. Review for:

1. **Unnecessary work**: redundant computations, repeated file reads,
   duplicate network/API calls, N+1 query patterns (especially with ORMs),
   re-sorting or re-filtering already-ordered data.

2. **Missed concurrency**: independent I/O-bound operations run sequentially
   that could use asyncio.gather, a ThreadPoolExecutor, or similar.

3. **Hot-path bloat**: new blocking work added to startup, request handlers,
   or tight loops — database calls, file I/O, heavy imports, or synchronous
   HTTP inside async paths.

4. **Recurring no-op updates**: unconditional state/cache writes inside
   polling loops or event handlers that fire even when the value hasn't
   changed. Flag missing change-detection guards.

5. **Unnecessary existence checks (TOCTOU)**: `os.path.exists()` or
   `Path.exists()` before open/delete/rename — operate directly and catch
   the exception instead.

6. **Memory**: unbounded lists/dicts/sets that grow without eviction,
   missing `__slots__` on high-frequency dataclasses, generator expressions
   that should stay lazy but are forced into lists, missing context-manager
   cleanup for file handles or DB connections.

7. **Overly broad operations**: `SELECT *` or loading entire files/objects
   when only a subset is needed; using `readlines()` or `read()` when
   iterating line-by-line suffices; fetching all DB rows to count them
   instead of `COUNT(*)`.

Format your output as a markdown list. Each item: file + line range,
efficiency category, and a concrete recommendation. If nothing to flag,
say "No efficiency issues found."
```

---

## Synthesis Instructions

After all three agents complete, produce a **unified review report** with
this structure:

```
## Code Review

### Code Reuse
<Agent 1 output, deduplicated>

### Code Quality
<Agent 2 output, deduplicated>

### Efficiency
<Agent 3 output, deduplicated>

### Summary
<2–4 sentence overall assessment. Call out the most critical finding, note
any patterns that appear across multiple agents, and state whether the code
is ready to merge or needs changes.>
```

Omit any section where the agent found nothing to flag. Keep the tone
direct and specific — no filler phrases.