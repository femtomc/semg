# TODO

Future analyses and features informed by Daniel Jackson's work at MIT CSAIL.

## DSM export

Add a Dependency Structure Matrix export format alongside Mermaid/DOT/JSON. A DSM is a square matrix where cell (i,j) indicates module i depends on module j. Reorganizing the matrix reveals architectural structure and highlights problematic dependencies.

Based on: Sangal, N. et al. (2005). "Using Dependency Models to Manage Complex Software Architecture." *OOPSLA '05*. [PDF](https://groups.csail.mit.edu/sdg/pubs/2005/oopsla05-dsm.pdf)

## Declarative architectural constraints

Let users define rules like `deny core/* -> ui/*` and check them against the graph. Report violations with the specific offending edges. This is the "design rules" idea from the DSM paper — the architect declares which dependencies are acceptable, and violations are detected as code evolves.

Based on: Sangal et al. (2005), and Alloy's constraint-checking philosophy from Jackson, D. (2012). *Software Abstractions: Logic, Language, and Analysis.* MIT Press.

## Minimal violation subgraphs

When reporting cycles, layering violations, or constraint violations, extract and display the smallest subgraph that demonstrates the problem. A concrete counterexample is more actionable than "violated: true."

Based on: Alloy's counterexample-driven analysis. Jackson, D. (2019). "Alloy: A Language and Tool for Exploring Software Designs." *CACM* 62(9), 66-76.

## Concept independence checking

Given user-defined module groupings (or community-detection-inferred clusters), measure cross-group coupling and flag violations of concept independence. Concepts should have zero direct dependencies on each other — all coordination should be through explicit sync points.

Based on: Jackson, D. (2021). *The Essence of Software: Why Concepts Matter for Design.* Princeton University Press. Meng, E. & Jackson, D. (2025). "What You See Is What It Does." *Onward! at SPLASH '25*. [arXiv](https://arxiv.org/abs/2508.14511)

## Overloaded module detection

Flag modules with high betweenness centrality AND edges into multiple otherwise-disconnected clusters. This is the graph-level signature of an "overloaded concept" — a module serving multiple unrelated purposes that should be split.

Based on: Jackson's concept design framework. The "overloaded concept" is the primary design smell in *Essence of Software*.

## Sync surface analysis

For module groupings, measure the "synchronization surface" — the cross-boundary edges:
- **Sync density**: cross-group edges / total edges
- **Sync asymmetry**: ratio of unidirectional vs bidirectional cross-group coupling
- **Sync fan-out**: how many other groups a given group synchronizes with

Based on: Meng & Jackson (2025). "What You See Is What It Does."

## References

- Sangal, N., Jordan, E., Sinha, V. & Jackson, D. (2005). "Using Dependency Models to Manage Complex Software Architecture." *OOPSLA '05*. [PDF](https://groups.csail.mit.edu/sdg/pubs/2005/oopsla05-dsm.pdf)
- Jackson, D. (2021). *The Essence of Software: Why Concepts Matter for Design.* Princeton University Press.
- Jackson, D. (2012). *Software Abstractions: Logic, Language, and Analysis.* MIT Press.
- Jackson, D. (2019). "Alloy: A Language and Tool for Exploring Software Designs." *CACM* 62(9), 66-76.
- Meng, E. & Jackson, D. (2025). "What You See Is What It Does." *Onward! at SPLASH '25*. [arXiv](https://arxiv.org/abs/2508.14511)
- O'Callahan, R. & Jackson, D. (1997). "Lackwit: A Program Understanding Tool Based on Type Inference." *ICSE '97*.
