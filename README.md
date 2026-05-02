# smg

`smg` turns source code into a semantic graph of modules, classes, functions, and their relationships. It already scans, queries, analyzes, diffs, exports, and enforces a useful baseline architecture graph for agents and humans.

Really, this is for LM agents -- it's supposed to give them better (and more efficient) eyes on the structure of your codebase,
and allow them to poke around and prod it as a graph of semantic entities.

It's not ... say, Smalltalk ... but it'll have to do for today.

## Installation

> **Warning:** the PyPI package named `smg` is "Shepherd Model Gateway," an
> unrelated project. Do **not** run `pip install smg` or `uv tool install smg`
> without `--from` — you will get the wrong package. Always install from this
> git repo or a local checkout.

Install as a CLI tool (all supported languages):

```bash
uv tool install smg \
  --from git+https://github.com/femtomc/smg \
  --with tree-sitter \
  --with tree-sitter-python \
  --with tree-sitter-javascript \
  --with tree-sitter-typescript \
  --with tree-sitter-c \
  --with tree-sitter-zig \
  --with xxhash \
  --with watchdog
```

Python-only variant (smaller install):

```bash
uv tool install smg \
  --from git+https://github.com/femtomc/smg \
  --with tree-sitter --with tree-sitter-python --with xxhash
```

For development from a local checkout:

```bash
git clone https://github.com/femtomc/smg && cd smg
uv pip install -e ".[scan,dev]"
```

## Quick start

```bash
cd your-project
smg init
smg scan src/

# Ask questions
smg about MyClass           # What is this?
smg usages MyClass          # Where is it used?
smg impact MyClass          # What breaks if I change it?
smg between api.routes db   # How do these relate?

# Analyze
smg analyze                 # Architectural analysis with hotspot detection
smg diff                    # What changed? (with rename/move detection)
smg export dsm > deps.csv   # Dependency Structure Matrix export
smg blame MyClass           # Who last touched this?
smg context MyClass --tokens 8000  # Pack source for an LLM prompt

# Enforce
smg rule add acyclic --invariant no-cycles
smg check                   # Enforce architectural rules
```

## Current release

This release ships the baseline architecture workflow plus three additions that
used to be release candidates:

- Quantified rules via `smg rule add --forall ... --assert ...`
- Declared concept/group analysis via `smg concept ...`, `smg analyze --concepts`, and `smg check`
- Minimal witnesses in `smg check` text and JSON output

Concept declarations can be inspected through `smg analyze --concepts` and
enforced in CI with `smg rule add boundaries --invariant concept-boundaries`.

## Contributor workflow

Run these commands from the repo root. `--with-editable .` keeps the CLI and imports pointed at your checkout while `uv` resolves the required extras on demand.

```bash
# Test suite
uv run --with-editable . --extra dev --extra scan python -m pytest -q

# Lint
uv run --with-editable . --extra dev --extra scan ruff check src tests

# Type checking
uv run --with-editable . --extra dev --extra scan python -m pyright
```

## Validation

The reproducible validation command for code changes in this repository is:

```bash
bash scripts/validate
```

The script runs Ruff, Pyright, pytest, optional native Zig checks, and the
dogfood `smg scan` / `smg check` / `smg analyze --concepts` flow. It also
ensures this repository's first-party concept declarations and
`concept-boundaries` rule are present in `.smg/` before running `smg check`.
To run those architecture checks by hand:

```bash
uv run --with-editable . --extra dev --extra scan python -m smg scan src tests --clean
uv run --with-editable . --extra dev --extra scan python -m smg check --format json
uv run --with-editable . --extra dev --extra scan python -m smg analyze --concepts --summary --format json
```

Concept declarations are stored in `.smg/concepts` when you add them locally.
If that file is absent, `smg analyze --concepts` reports an empty declaration
set instead of mutating `.smg/graph.jsonl`.

CI runs the same script through `.github/workflows/validate.yml`. Pyright is
expected to report `0 errors, 0 warnings`; optional-member access is configured
as an error so nullable tree-sitter and graph lookups stay explicit.

## Scan benchmarking

Use the scan benchmark when changing extraction, resolution, or parallel scan
behavior:

```bash
uv run --with-editable . --extra dev --extra scan python benchmarks/bench_mp.py --samples 5 --jobs 1,2,4,8
```

The benchmark creates a fresh in-memory graph for each sample and reports
median end-to-end scan time, not just parser extraction time.

## The graph

`smg` stores a graph of code entities in `.smg/graph.jsonl` at your project root. Each line is a node (module, class, function, ...) or a typed edge (contains, calls, imports, inherits, ...).

There are three ways to populate the graph:

1. **`smg scan`** -- tree-sitter parses source files and extracts symbols, containment, imports, inheritance, and call graph automatically.
2. **Manual CLI** -- agents or humans add nodes and edges directly with `smg add` and `smg link`.
3. **Both** -- scan for the baseline, then layer on domain-specific relationships (e.g. "tests", "endpoint", custom types).

Every node and edge is tagged with `source: "scan"` or `source: "manual"`. When rescanning, only scan-sourced nodes are cleaned -- manual annotations survive. If a rescan deletes a node that had manual edges, those orphaned edges are reported.

When a scan cannot resolve an extracted edge, the summary reports the skipped
edge count. JSON output also includes a bounded `skipped_edge_samples` list with
the source, relationship, target, reason, and category for representative
skipped edges, so agents can see whether the scan scope is too narrow, a call is
external, or a resolver needs work.

### Supported languages

| Language | Extensions | Grammar |
|----------|-----------|---------|
| Python | `.py` | `tree-sitter-python` |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | `tree-sitter-javascript` |
| TypeScript | `.ts`, `.tsx` | `tree-sitter-typescript` |
| C | `.c`, `.h` | `tree-sitter-c` |
| C++ | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`, and C++-style `.h` headers | `tree-sitter-cpp` |
| CUDA | `.cu`, `.cuh` | `tree-sitter-cpp` |
| Metal | `.metal` | `tree-sitter-cpp` |
| Zig | `.zig` | `tree-sitter-zig` |

All languages extract: classes/structs, functions, methods, constants, containment, imports, inheritance, call graph, and per-function metrics. The C++ extractor also recognizes common header-only shapes such as class declarations, templates, and out-of-class member definitions. Adding a language means writing a `langs/<language>.py` extractor and a `BranchMap` -- the metrics engine and scanner are shared.

## Structural hashing

Source code has two kinds of identity: its exact text (what you see in a file), and its structure (what the compiler sees after discarding names, whitespace, and comments). `smg` computes both during extraction and stores them on every function, method, and class node:

- **Content hash** -- xxHash64 of the entity's raw source bytes. Two entities with the same content hash have identical source code, byte for byte.
- **Structure hash** -- xxHash64 of a normalized AST walk that strips comments, identifiers, and literals but preserves node types and nesting. Two entities with the same structure hash have the same control flow shape -- the same branches, loops, and call structure -- even if variable names, strings, or comments differ.

This separation is the key design decision. A content hash tells you "these are the same code." A structure hash tells you "these do the same thing, structurally." The gap between them is where renames, comment edits, and cosmetic refactors live.

### Relationship to content-addressed code

The [Unison language][unison] pioneered the idea that code should be identified by the hash of its AST, not by its name. In Unison, a function's hash *is* its identity -- names are just human-readable labels in a separate namespace. This makes rename refactoring free (renaming updates a mapping, not code) and eliminates rebuilds (cache everything by hash, invalidate nothing unless the definition changes). The idea is described in Unison's documentation and draws on de Bruijn's name-free representation of bound variables ([de Bruijn, 1972][debruijn]).

`smg` applies the same core insight -- structural identity independent of names -- but in a different context and at a different fidelity:

- **Unison hashes for identity.** The hash is authoritative and permanent. It includes types, de Bruijn indices for local variables, and recursive hashes of all dependencies. Unison can do this because it controls the compiler and type system.
- **smg hashes for detection.** The hash is a heuristic for matching entities across graph snapshots. It includes AST node types and nesting structure but not types or dependency hashes, because smg operates as an external analysis tool across languages without full semantic analysis.
- **smg keeps both hashes.** Unison collapses "same code" and "same structure, different names" into one hash (both are the same identity). smg distinguishes them because `smg diff` wants to *report* that a rename happened -- the content hash says "the code changed," the structure hash says "but the shape didn't," and together they tell you it was a rename.

### How hashes are used

Structural hashing powers three things:

1. **Rename/move detection in `smg diff`** -- when a function disappears and a structurally identical one appears elsewhere, they are matched as a rename rather than a deletion + addition (see [Structural diff](#structural-diff)).
2. **Change classification** -- same content hash means no real change (metadata-only update). Same structure hash with different content hash means a cosmetic change (rename, comment edit). Different structure hash means a real structural change.
3. **Stability tracking** -- structure hashes persist in the graph across scans, so you can track which entities have structurally changed over time, independent of formatting or naming churn.

## Architectural analysis

`smg analyze` runs graph-theoretic, OO, smell-detection, and git-churn analyses in a single pass, then synthesizes a ranked list of hotspots.

```bash
smg analyze                        # all analyses
smg analyze --module auth          # scope to auth.* nodes
smg analyze --since HEAD~5         # only analyze changed nodes
smg analyze --summary --top 5      # hotspots and key findings only
smg analyze --churn-days 180       # look back 6 months for churn (default: 90)
smg analyze --format json          # structured output for agents
```

### Graph-theoretic analyses

| Analysis | What it finds | Why it matters |
|----------|--------------|----------------|
| Cycle detection | Circular dependencies between modules/classes. Uses Tarjan's algorithm for strongly connected components. [Tarjan (1972)][tarjan] | Cycles prevent independent deployment and testing -- they force you to change and release coupled components together. |
| Topological layering | Assigns each node a layer based on dependency depth (layer 0 = leaves with no outgoing deps). | Reveals the architecture's depth. Tall, narrow layer stacks suggest long dependency chains; wide layers suggest parallel modules. |
| PageRank | Ranks nodes by recursive importance -- a node is important if important nodes depend on it. [Brin & Page (1998)][pagerank] | Identifies load-bearing abstractions: the modules that, if broken, cascade failures through the most dependents. |
| Betweenness centrality | Measures how often a node lies on shortest paths between other nodes. [Brandes (2001)][brandes] | Nodes with high betweenness are structural bottlenecks -- information and control flow must pass through them. Changing them has outsized risk. |
| k-core decomposition | Finds the maximal subgraph where every node has at least _k_ connections. [Seidman (1983)][seidman] | The innermost core is the tightly coupled heart of the architecture. If it's large, the system may be hard to decompose. |
| Bridge detection | Edges whose removal disconnects part of the graph. | Bridges are fragile -- they represent sole paths between components. Redundant paths (no bridges) indicate a more resilient architecture. |
| Fan-in / fan-out | Per-node counts of incoming (fan-in) and outgoing (fan-out) coupling edges. | High fan-in means a node is heavily depended on (risky to change). High fan-out means a node depends on many others (sensitive to their changes). |
| Dead code detection | Nodes with zero incoming coupling edges (no callers, no importers), excluding modules, packages, and known entry points. | Dead code inflates the codebase without providing value. Removing it reduces maintenance burden and cognitive load. |
| Layering violations | Coupling edges where the source is at the same or lower topological layer than the target -- back-dependencies. | These are the specific edges that create cycles or violate the intended dependency flow. They tell you which edges to remove to restore clean layering. |

### OO metrics

The CK suite ([Chidamber & Kemerer, 1994][ck]) and Martin's package metrics ([Martin, 1994][martin]) quantify class-level and module-level design quality.

| Metric | Per | Description |
|--------|-----|-------------|
| WMC | class | Weighted Methods per Class -- sum of cyclomatic complexity of all methods. High WMC indicates a class that does too much. |
| DIT | class | Depth of Inheritance Tree -- how many ancestors a class has. Deep trees increase complexity and fragility. |
| NOC | class | Number of Children -- direct subclass count. Many children suggest a class is a key abstraction (or overused as a base). |
| CBO | class | Coupling Between Objects -- number of distinct external classes this class couples to. High CBO makes classes hard to reuse and test. |
| RFC | class | Response For a Class -- methods in the class plus distinct methods they directly call. High RFC means more potential behavior to test. |
| LCOM4 | class | Lack of Cohesion of Methods -- number of connected components in the intra-class method call graph. LCOM4 > 1 means the class has disjoint responsibilities and should likely be split. [Hitz & Montazeri (1995)][lcom4] |
| Ca / Ce | module | Afferent (incoming) / efferent (outgoing) coupling -- how many other modules depend on this one, and how many it depends on. |
| Instability | module | Ce / (Ca + Ce). Ranges from 0 (stable, heavily depended upon) to 1 (unstable, depends on others). |
| Abstractness | module | Ratio of interfaces to total classes. Ranges from 0 (all concrete) to 1 (all abstract). |
| Distance | module | \|A + I - 1\| -- distance from the "main sequence" line where A + I = 1. Modules far from this line are either too abstract for their stability or too concrete for their instability. |
| SDP violations | module | Cases where a stable module depends on an unstable module, violating the Stable Dependencies Principle. Dependencies should flow toward stability. |

### Per-function metrics

Every function and method node includes AST-based metrics in its metadata, computed automatically during scan:

| Metric | Description |
|--------|-------------|
| `cyclomatic_complexity` | Number of linearly independent paths through a function. 1 + branches + boolean operators. [McCabe (1976)][mccabe] |
| `cognitive_complexity` | Branches weighted by nesting depth -- penalizes deeply nested logic more than flat branching. [Campbell (2018)][cognitive] |
| `max_nesting_depth` | Deepest control flow nesting level |
| `lines_of_code` | Function body line count |
| `parameter_count` | Number of parameters |
| `return_count` | Number of return statements |
| `fan_in` | How many functions call this one |
| `fan_out` | How many functions this one calls |

Language-agnostic -- metrics are computed from tree-sitter ASTs using a per-language `BranchMap` that maps node types to semantic roles.

### Code smells

| Smell | Detection rule | What it means |
|-------|---------------|---------------|
| God Class | WMC >= 20 AND CBO >= 5 AND LCOM4 >= 2 | A class with too many responsibilities -- complex, coupled, and incohesive. Should be split. [Fowler (1999)][fowler] |
| Feature Envy | Method references another class's members more than its own (>= 2 external refs) | The method probably belongs in the other class. Moving it improves cohesion in both classes. |
| Shotgun Surgery | Function/method with coupling fan-out >= 7 | Changing this function likely requires coordinated changes across many dependents. Reducing fan-out isolates change. |
| God File | Module with high total cyclomatic complexity, many functions, or many external concerns | A file doing too much. Split into focused modules to reduce cognitive load and merge conflicts. |

### Hotspot synthesis

All analyses feed into a composite **hotspot ranking** that scores nodes by a weighted combination of complexity (WMC, cyclomatic complexity), coupling (CBO, fan-out), cohesion (LCOM4), centrality (betweenness, PageRank), churn (commit touch frequency), and module distance from the main sequence. The resulting ranked list surfaces the areas most likely to cause problems when modified.

## Change tracking

`smg` integrates structural graph analysis with git history to track how code changes over time.

### Structural diff

`smg diff` compares the current graph against a git ref and reports structural changes: added, removed, changed, and **renamed/moved** nodes and edges.

```bash
smg diff              # vs HEAD (last commit)
smg diff HEAD~3       # vs 3 commits ago
smg diff main         # vs another branch
```

Rename and move detection uses a three-phase matching algorithm on unmatched added/removed nodes:

1. **Content hash match** -- if a removed node and an added node have identical source bytes (same content hash), the entity was purely renamed or moved with no code changes. Reported as an "exact" match.
2. **Structure hash match** -- if there's a unique structural match (same AST shape but different content, e.g. a variable was renamed inside the function), the entity was renamed with minor edits. Reported as a "structural" match.
3. **Fuzzy name similarity** -- for still-unmatched entities of the same type, smg computes [Jaccard similarity][jaccard] on whitespace-split tokens of their fully-qualified names. Pairs above 0.8 similarity are classified as renamed/moved. This catches cases where both the entity's name and its body changed significantly but the name tokens mostly overlap (e.g. `app.utils.parse_config` -> `app.helpers.parse_config`).

Anything unmatched after all three phases is reported as a plain addition or deletion.

### Git churn

`smg analyze` integrates temporal data from git history. For each entity in the graph, it counts how many commits modified the entity's line range over a configurable time window (default: 90 days).

Entities that are both structurally central (high complexity, high coupling) AND frequently changed are the most dangerous hotspots -- they are hard to modify correctly AND are being modified often. Churn feeds into the hotspot ranking at both class level (> 10 touches) and function level (> 5 touches with cyclomatic complexity > 10).

```bash
smg analyze --churn-days 30   # last month only
smg analyze --churn-days 365  # full year
```

### Entity-level blame

`smg blame` maps graph entities to the most recent git commit that touched their source lines:

```bash
# Single entity
smg blame SemGraph
# smg.graph.SemGraph [class]
#   src/smg/graph.py:14-251
#   69a55085a058 user@email.com (2024-01-15)
#   Optimize scan and analysis hot paths

# All entities in a file
smg blame src/smg/graph.py
```

For a single entity, it runs `git log -1 -L <start>,<end>:<file>` to find the commit that last modified the entity's line range. For a file, it blames every entity in that file and displays a table sorted by line number. Use `--format json` for structured output.

## Context budgeting

When preparing a prompt for an LLM, the question is: which source code should go in, given a limited context window? `smg context` uses the dependency graph to answer this -- it packs relevant source code into a token budget, prioritizing by graph proximity to a target entity.

```bash
smg context auth.service --tokens 8000
```

The algorithm walks outward from the target:

1. **Target entity** -- full source (always included, even if it exceeds the budget alone)
2. **Direct dependencies and dependents** (1-hop) -- full source, downgraded to signature if over budget
3. **2-hop neighbors** -- signature only (the function/class declaration line)
4. **3-hop neighbors** -- summary only (name, type, file location, docstring)

If the budget fills up at any tier, remaining entries are downgraded to the next level. Use `--format json` for structured output; the default is compact text in both terminals and pipes. The token counter defaults to `len(text) / 4` (~4 chars per token) but is pluggable via the library API.

## Architectural constraints

`smg analyze` tells you what *is* true about your architecture. `smg rule` and `smg check` let you declare what *should* be true -- and find where the code departs from intent.

This is inspired by Alloy's approach to software design ([Jackson, 2012][alloy]): state structural properties declaratively, then check them automatically. When a rule is violated, smg reports the specific offending edges or nodes -- concrete counterexamples, not just "violated: true."

### Path denial rules

Forbid coupling edges that match a glob pattern:

```bash
# No module in infra/ may depend on app/
smg rule add layering --deny "infra.* -> app.*"

# Controllers must not directly call repository code
smg rule add no-direct-db --deny "*.controller -[calls]-> *.repository"
```

Patterns use `fnmatch` syntax over fully-qualified node names. The optional `[rel]` filter restricts to a specific relationship type; without it, all coupling edges (calls, imports, inherits, implements, depends_on) are checked.

### Structural invariants

```bash
smg rule add acyclic --invariant no-cycles
smg rule add reachable --invariant no-dead-code --entry-points "main,cli.*"
smg rule add layered --invariant no-layering-violations
smg rule add boundaries --invariant concept-boundaries
```

| Invariant | What it checks |
|-----------|---------------|
| `concept-boundaries` | Declared concepts only communicate through their `--sync-point` prefixes |
| `no-cycles` | No circular dependencies (Tarjan's SCC) |
| `no-dead-code` | Every non-entry node has at least one incoming coupling edge |
| `no-layering-violations` | No back-dependency edges violate topological layering |

### Quantified rules

```bash
smg rule add service-fan-out --forall "*.service" --assert "fan_out <= 5"
smg rule add simple-handlers --forall "api.handlers.*" --assert "cyclomatic_complexity <= 10"
```

Quantified rules match subjects by glob and evaluate a small expression language
over graph metrics, OO metrics, and scanner metadata. The current release
supports per-subject universal checks; it does not yet support graph-wide
aggregates such as `count(...)` or `sum(...)`.

### Checking rules

```bash
smg check                  # check all rules
smg check layering         # check one rule
smg check --format json    # structured output for agents / CI
```

Exit code 0 means all rules pass; exit code 1 means at least one violation. This makes `smg check` suitable as a CI gate:

```bash
smg scan src/ --clean && smg check
```

Failures include a `witnesses` list in JSON and show the first few witnesses in
text output. Deny and layering rules emit edge witnesses, cycle rules emit
minimal cycles, and quantified rules emit predicate witnesses with the observed
metric facts.

Rules are stored as JSONL records in `.smg/rules`. The file is created on first
use and stays separate from `.smg/graph.jsonl`.

## Concept and group analysis

Declare higher-level module groups in a sidecar store, then lift the dependency
graph to concept-level summaries and cross-concept witnesses.

```bash
smg concept add cli --prefix smg.cli
smg concept add core --prefix smg.graph --prefix smg.model
smg concept add core-api --prefix smg.api --sync-point smg.api.boundary
smg concept sync-point core-api smg.api.public
smg concept list
smg analyze --concepts
```

Concept declarations live in `.smg/concepts`, not in `.smg/graph.jsonl`. Use
`--sync-point` to mark node-name prefixes that are allowed to cross a concept
boundary, or add one later with `smg concept sync-point NAME PREFIX`. The
`concepts` section is added to `smg analyze --format json` only when
`--concepts` is requested.

See [Architecture Concepts](docs/architecture-concepts.md) for the first-party
calibration workflow and suggested sync-point candidates.

To enforce declared concept boundaries, add the concept invariant:

```bash
smg rule add boundaries --invariant concept-boundaries
smg check --format json
```

## Agent usage

Agents should treat `smg` as a codebase database. The typical workflow:

```bash
# Orient
smg overview                           # graph stats, top connected nodes
smg about auth.service                 # context card for a node

# Investigate
smg usages auth.service                # who uses this?
smg impact auth.service                # what depends on this?
smg between api.routes db.models       # how do these connect?
smg diff                               # what changed since last commit?
smg blame auth.service                 # who last touched this?
smg analyze --summary --top 5          # key architectural findings
smg query deps auth.service            # forward transitive dependencies

# Build LLM context
smg context auth.service --tokens 8000 # source packed by graph proximity

# Enforce
smg rule add layering --deny "infra.* -> app.*"
smg check                              # exit 1 on violation

# Mutate
smg add endpoint /api/login --doc "Login endpoint" --meta method=POST
smg link api.routes calls auth.service
smg scan --changed                     # incremental rescan
```

Commands emit compact text by default in both terminals and pipes. Use
`--format json` or `--json` on commands that support structured output.

## Commands

### Explore

| Command | Purpose |
|---------|---------|
| `smg about <name> [--depth 0\|1\|2]` | Context card with progressive detail |
| `smg usages <name>` | Where is X used? All direct references with source location |
| `smg impact <name> [--depth N]` | Reverse transitive impact analysis |
| `smg between <A> <B>` | Shortest path + direct edges |
| `smg overview [--top N]` | Graph stats + most connected nodes |
| `smg diff [REF]` | Structural diff with rename/move detection (default: HEAD) |
| `smg analyze [--top N] [--module PREFIX] [--summary] [--concepts] [--churn-days N]` | Architectural analysis with hotspot detection |
| `smg context <name> [--tokens N]` | Pack source code for LLM context within a token budget |
| `smg blame <name\|file>` | Entity-level git blame: who last touched this? |

### Enforce

| Command | Purpose |
|---------|---------|
| `smg rule add <name> --deny "PATTERN"` | Forbid edges matching a glob pattern |
| `smg rule add <name> --invariant TYPE` | Require a structural invariant |
| `smg rule add <name> --forall "GLOB" --assert "EXPR"` | Enforce a per-subject metric budget |
| `smg rule list` | List all rules |
| `smg rule rm <name>` | Remove a rule |
| `smg concept add <name> --prefix PREFIX [--sync-point PREFIX]...` | Declare a concept/group boundary |
| `smg concept list` | List all concept declarations |
| `smg concept rm <name>` | Remove a concept declaration |
| `smg check [NAME]` | Check all rules (or one). Exit 1 on violation. |

### Inspect

| Command | Purpose |
|---------|---------|
| `smg show <name>` | Node details + connections + metrics |
| `smg list [--type TYPE]` | List nodes |
| `smg status` | Node/edge count breakdown |
| `smg query deps <name>` | Transitive dependencies |
| `smg query callers <name>` | What calls this? |
| `smg query path <A> <B>` | Shortest path |
| `smg query subgraph <name> [--depth N]` | N-hop neighborhood |
| `smg query incoming <name> [--rel TYPE]` | Incoming edges |
| `smg query outgoing <name> [--rel TYPE]` | Outgoing edges |
| `smg validate` | Check graph integrity |

### Mutate

| Command | Purpose |
|---------|---------|
| `smg init` | Create `.smg/` in current directory |
| `smg scan [PATH...] [--clean] [--exclude GLOB]` | Auto-populate from source via tree-sitter |
| `smg scan [PATH...] --jobs N` | Extract files in parallel worker processes, then merge deterministically |
| `smg scan --changed` | Incremental: rescan files changed since HEAD |
| `smg scan --since REF` | Incremental: rescan files changed since a git ref |
| `smg watch [PATH...]` | Auto-rescan on file changes (Ctrl+C to stop) |
| `smg add <type> <name> [--file --line --doc --meta K=V]` | Add/upsert a node |
| `smg link <source> <rel> <target>` | Add an edge |
| `smg rm <name>` | Remove a node + all its edges |
| `smg unlink <source> <rel> <target>` | Remove an edge |
| `smg update <name> [--type --file --line --doc --meta K=V]` | Update node fields |
| `smg batch` | JSONL commands from stdin, one load/save cycle |

### Export

| Command | Purpose |
|---------|---------|
| `smg export json [--indent]` | Full graph as JSON |
| `smg export mermaid` | Mermaid flowchart |
| `smg export dot` | Graphviz DOT |
| `smg export text` | Human-readable listing |
| `smg export dsm [--level module\|class\|all]` | Dependency Structure Matrix as CSV |

## Reference

### Node types

`package`, `module`, `class`, `function`, `method`, `interface`, `variable`, `constant`, `type`, `endpoint`, `config` -- plus any custom string.

### Relationship types

`calls`, `inherits`, `implements`, `contains`, `depends_on`, `imports`, `returns`, `accepts`, `overrides`, `decorates`, `tests` -- plus any custom string.

### Data format

`.smg/graph.jsonl` -- one JSON object per line:

```jsonl
{"kind":"node","name":"app.core.Engine","type":"class","file":"src/app/core.py","line":12,"metadata":{"source":"scan","content_hash":"a1b2c3d4e5f6a7b8","structure_hash":"f8e7d6c5b4a39281","metrics":{...}}}
{"kind":"edge","source":"app.core","rel":"contains","target":"app.core.Engine","metadata":{"source":"scan"}}
```

Rules and concept declarations use separate JSONL sidecars, created on first
use:

```jsonl
{"kind":"rule","name":"service-fan-out","type":"quantified","selector":"*.service","assertion":"fan_out <= 5"}
{"kind":"concept","name":"cli","prefixes":["app.cli"],"sync_points":["app.cli.surface"]}
```

Keeping rules and concepts out of `.smg/graph.jsonl` preserves graph counts and
analyses across rescans. All three stores are human-readable and written
atomically via temp file + rename.

### Name resolution

Node names are fully qualified (`app.core.Engine.run`), but you can use short names:

```bash
smg about Engine          # Matches app.core.Engine if unambiguous
smg show run              # Error if multiple matches -- lists candidates
```

### Excluding files

`smg scan` skips common non-source directories by default (`.git`, `node_modules`, `__pycache__`, `.venv`, etc.). Extend with `--exclude GLOB` flags or a `.smgignore` file at the project root (gitignore syntax).

### Design principles

- **Agent-first**: explicit JSON output, compact text defaults, exit codes for branching
- **Gradual disclosure**: `about` -> `show` -> `query` -- start simple, drill down as needed
- **Language-agnostic**: tree-sitter grammars for any language, BranchMap protocol for metrics
- **Incremental**: `--changed` rescans only modified files, `watch` for live updates
- **Provenance-aware**: scan vs manual annotations tracked, manual edges survive rescans
- **Zero config**: `smg init && smg scan .` works on any supported project
- **Git-friendly**: JSONL is diffable, sorted deterministically, written atomically

## References

- de Bruijn, N.G. (1972). [Lambda Calculus Notation with Nameless Dummies](https://doi.org/10.1016/1385-7258(72)90034-0). *Indagationes Mathematicae*, 75(5), 381--392.
- McCabe, T.J. (1976). [A Complexity Measure](https://doi.org/10.1109/TSE.1976.233837). *IEEE TSE*, SE-2(4), 308--320.
- Tarjan, R.E. (1972). [Depth-First Search and Linear Graph Algorithms](https://doi.org/10.1137/0201010). *SIAM Journal on Computing*, 1(2), 146--160.
- Seidman, S.B. (1983). [Network Structure and Minimum Degree](https://doi.org/10.1016/0378-8733(83)90028-X). *Social Networks*, 5(3), 269--287.
- Chidamber, S.R. & Kemerer, C.F. (1994). [A Metrics Suite for Object Oriented Design](https://doi.org/10.1109/32.295895). *IEEE TSE*, 20(6), 476--493.
- Martin, R.C. (1994). [OO Design Quality Metrics: An Analysis of Dependencies](https://doi.org/10.1007/978-1-4612-4316-3_9). *OOPSLA '94 Workshop*.
- Hitz, M. & Montazeri, B. (1995). [Measuring Coupling and Cohesion in Object-Oriented Systems](https://scholar.google.com/scholar?q=Hitz+Montazeri+1995+Measuring+Coupling+Cohesion). *Proc. ISACC '95*.
- Brin, S. & Page, L. (1998). [The Anatomy of a Large-Scale Hypertextual Web Search Engine](https://doi.org/10.1016/S0169-7552(98)00110-X). *Computer Networks*, 30(1--7), 107--117.
- Fowler, M. (1999). [Refactoring: Improving the Design of Existing Code](https://martinfowler.com/books/refactoring.html). Addison-Wesley.
- Brandes, U. (2001). [A Faster Algorithm for Betweenness Centrality](https://doi.org/10.1080/0022250X.2001.9990249). *Journal of Mathematical Sociology*, 25(2), 163--177.
- Jaccard, P. (1912). [The Distribution of the Flora in the Alpine Zone](https://doi.org/10.1111/j.1469-8137.1912.tb05611.x). *New Phytologist*, 11(2), 37--50.
- Jackson, D. (2012). [Software Abstractions: Logic, Language, and Analysis](https://mitpress.mit.edu/9780262017152/). MIT Press.
- Campbell, G.A. (2018). [Cognitive Complexity: An Overview and Evaluation](https://doi.org/10.1145/3194164.3194186). *Proc. TechDebt '18*, ACM.

[debruijn]: https://doi.org/10.1016/1385-7258(72)90034-0
[mccabe]: https://doi.org/10.1109/TSE.1976.233837
[tarjan]: https://doi.org/10.1137/0201010
[seidman]: https://doi.org/10.1016/0378-8733(83)90028-X
[ck]: https://doi.org/10.1109/32.295895
[martin]: https://doi.org/10.1007/978-1-4612-4316-3_9
[lcom4]: https://scholar.google.com/scholar?q=Hitz+Montazeri+1995+Measuring+Coupling+Cohesion
[jaccard]: https://doi.org/10.1111/j.1469-8137.1912.tb05611.x
[pagerank]: https://doi.org/10.1016/S0169-7552(98)00110-X
[fowler]: https://martinfowler.com/books/refactoring.html
[brandes]: https://doi.org/10.1080/0022250X.2001.9990249
[alloy]: https://mitpress.mit.edu/9780262017152/
[cognitive]: https://doi.org/10.1145/3194164.3194186
[unison]: https://www.unison-lang.org/docs/the-big-idea/

## License

MIT
