# TODO

Future analyses and features. Items marked [done] have been shipped.

## [done] Declarative architectural constraints

Shipped in `smg rule` / `smg check`. Path denial rules and structural invariants (no-cycles, no-dead-code, no-layering-violations) with counterexample-driven output.

## DSM export

Add a Dependency Structure Matrix export format alongside Mermaid/DOT/JSON. A DSM is a square matrix where cell (i,j) indicates module i depends on module j. Reorganizing the matrix reveals architectural structure and highlights problematic dependencies.

Based on: Sangal, N. et al. (2005). "Using Dependency Models to Manage Complex Software Architecture." *OOPSLA '05*. [PDF](https://groups.csail.mit.edu/sdg/pubs/2005/oopsla05-dsm.pdf)

## Minimal violation subgraphs

When reporting cycles, layering violations, or constraint violations, extract and display the smallest subgraph that demonstrates the problem. A concrete counterexample is more actionable than "3 cycle(s)."

Based on: Alloy's counterexample-driven analysis. Jackson, D. (2019). "Alloy: A Language and Tool for Exploring Software Designs." *CACM* 62(9), 66-76.

## Community detection (label propagation)

Find natural module clusters from the coupling graph structure. Compare against declared package boundaries to find misplaced code. Simpler than Louvain, still useful for identifying modules that don't match their declared homes.

## Concept independence checking

Given user-defined module groupings (or community-detection-inferred clusters), measure cross-group coupling and flag violations of concept independence. Concepts should have zero direct dependencies on each other -- all coordination should be through explicit sync points.

Based on: Jackson, D. (2021). *The Essence of Software: Why Concepts Matter for Design.* Princeton University Press. Meng, E. & Jackson, D. (2025). "What You See Is What It Does." *Onward! at SPLASH '25*. [arXiv](https://arxiv.org/abs/2508.14511)

## Overloaded module detection

Flag modules with high betweenness centrality AND edges into multiple otherwise-disconnected clusters. This is the graph-level signature of an "overloaded concept" -- a module serving multiple unrelated purposes that should be split.

Based on: Jackson's concept design framework. The "overloaded concept" is the primary design smell in *Essence of Software*.

## Sync surface analysis

For module groupings, measure the "synchronization surface" -- the cross-boundary edges:
- **Sync density**: cross-group edges / total edges
- **Sync asymmetry**: ratio of unidirectional vs bidirectional cross-group coupling
- **Sync fan-out**: how many other groups a given group synchronizes with

Based on: Meng & Jackson (2025). "What You See Is What It Does."

## HITS (Hub/Authority)

Kleinberg's algorithm. Distinguishes hubs (orchestrators that call many things) from authorities (core utilities called by many). Different signal from PageRank -- same iterative power-method pattern.

## Change coupling (co-change analysis)

Nodes that always change together in git history but have no structural edge. Requires integrating `git log`. Surfaces hidden dependencies that static analysis misses.

## Quantified constraint rules (Layer 3)

Extend the rule system beyond deny/invariant to support quantified constraints:
```
smg rule add service-fan-out --forall "*.service" --assert "fan_out <= 5"
```
Requires a small expression language over node properties and graph metrics. Design carefully to avoid replicating Alloy's learning curve.

## References

- Sangal, N., Jordan, E., Sinha, V. & Jackson, D. (2005). "Using Dependency Models to Manage Complex Software Architecture." *OOPSLA '05*. [PDF](https://groups.csail.mit.edu/sdg/pubs/2005/oopsla05-dsm.pdf)
- Jackson, D. (2021). *The Essence of Software: Why Concepts Matter for Design.* Princeton University Press.
- Jackson, D. (2012). *Software Abstractions: Logic, Language, and Analysis.* MIT Press.
- Jackson, D. (2019). "Alloy: A Language and Tool for Exploring Software Designs." *CACM* 62(9), 66-76.
- Meng, E. & Jackson, D. (2025). "What You See Is What It Does." *Onward! at SPLASH '25*. [arXiv](https://arxiv.org/abs/2508.14511)
- O'Callahan, R. & Jackson, D. (1997). "Lackwit: A Program Understanding Tool Based on Type Inference." *ICSE '97*.
