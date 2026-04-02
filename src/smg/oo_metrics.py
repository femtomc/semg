"""Object-oriented and module-level architectural metrics.

Computes CK metrics suite (WMC, DIT, NOC, CBO, RFC, LCOM4) per class,
and Martin's package metrics (Ca, Ce, Instability, Abstractness, Distance)
per module. All functions are pure — they take a SemGraph and return dicts.
"""
from __future__ import annotations

from collections import defaultdict

from smg.graph import SemGraph
from smg.model import NodeType, RelType

_COUPLING_RELS = frozenset({
    RelType.CALLS.value,
    RelType.IMPORTS.value,
    RelType.INHERITS.value,
    RelType.IMPLEMENTS.value,
    RelType.DEPENDS_ON.value,
})

_METHOD_TYPES = frozenset({NodeType.FUNCTION.value, NodeType.METHOD.value})


def _class_members(graph: SemGraph, class_name: str) -> list[str]:
    """Get all direct member names of a class (methods, functions, constants)."""
    return [e.target for e in graph.outgoing(class_name, rel=RelType.CONTAINS)]


def _class_methods(graph: SemGraph, class_name: str) -> list[str]:
    """Get method/function names contained in a class."""
    members = _class_members(graph, class_name)
    return [m for m in members if graph.get_node(m) and graph.get_node(m).type.value in _METHOD_TYPES]


def _module_members(graph: SemGraph, module_name: str) -> set[str]:
    """Get all nodes transitively contained in a module (including class members)."""
    result: set[str] = set()
    queue = [module_name]
    while queue:
        current = queue.pop()
        for edge in graph.outgoing(current, rel=RelType.CONTAINS):
            if edge.target not in result:
                result.add(edge.target)
                queue.append(edge.target)
    return result


def _containing_class(graph: SemGraph, node_name: str) -> str | None:
    """Find the class that contains this node (one level up via CONTAINS)."""
    for edge in graph.incoming(node_name, rel=RelType.CONTAINS):
        parent = graph.get_node(edge.source)
        if parent and parent.type == NodeType.CLASS:
            return edge.source
    return None


def _containing_module(graph: SemGraph, node_name: str) -> str | None:
    """Find the module/package that ultimately contains this node."""
    current = node_name
    while True:
        parents = [e.source for e in graph.incoming(current, rel=RelType.CONTAINS)]
        if not parents:
            return None
        parent = parents[0]
        pnode = graph.get_node(parent)
        if pnode and pnode.type.value in ("module", "package"):
            return parent
        current = parent


# --- CK Metrics ---


def wmc(graph: SemGraph) -> dict[str, int]:
    """Weighted Methods per Class: sum of cyclomatic complexity of all methods."""
    result: dict[str, int] = {}
    for node in graph.all_nodes(type=NodeType.CLASS):
        methods = _class_methods(graph, node.name)
        total = 0
        for m in methods:
            mnode = graph.get_node(m)
            if mnode:
                cc = mnode.metadata.get("metrics", {}).get("cyclomatic_complexity", 1)
                total += cc
        result[node.name] = total
    return result


def dit(graph: SemGraph) -> dict[str, int]:
    """Depth of Inheritance Tree: max chain length from class to root via INHERITS."""
    result: dict[str, int] = {}
    cache: dict[str, int] = {}

    def _dit(name: str) -> int:
        if name in cache:
            return cache[name]
        cache[name] = 0  # prevent infinite recursion on cycles
        parents = [e.target for e in graph.outgoing(name, rel=RelType.INHERITS)]
        if not parents:
            cache[name] = 0
        else:
            cache[name] = 1 + max(_dit(p) for p in parents)
        return cache[name]

    for node in graph.all_nodes(type=NodeType.CLASS):
        result[node.name] = _dit(node.name)
    return result


def noc(graph: SemGraph) -> dict[str, int]:
    """Number of Children: count of direct subclasses."""
    result: dict[str, int] = {}
    for node in graph.all_nodes(type=NodeType.CLASS):
        children = [e.source for e in graph.incoming(node.name, rel=RelType.INHERITS)]
        result[node.name] = len(children)
    return result


def cbo(graph: SemGraph) -> dict[str, int]:
    """Coupling Between Objects: distinct external classes this class is coupled to.

    Counts classes referenced by or referencing this class's members via coupling edges.
    """
    result: dict[str, int] = {}

    for node in graph.all_nodes(type=NodeType.CLASS):
        members = set(_class_members(graph, node.name))
        members.add(node.name)
        coupled_classes: set[str] = set()

        for member in members:
            # Outgoing coupling edges
            for edge in graph.outgoing(member):
                if edge.rel.value in _COUPLING_RELS:
                    target_class = _containing_class(graph, edge.target)
                    if target_class and target_class != node.name:
                        coupled_classes.add(target_class)
                    elif edge.target != node.name and graph.get_node(edge.target) and graph.get_node(edge.target).type == NodeType.CLASS:
                        coupled_classes.add(edge.target)

            # Incoming coupling edges
            for edge in graph.incoming(member):
                if edge.rel.value in _COUPLING_RELS:
                    source_class = _containing_class(graph, edge.source)
                    if source_class and source_class != node.name:
                        coupled_classes.add(source_class)
                    elif edge.source != node.name and graph.get_node(edge.source) and graph.get_node(edge.source).type == NodeType.CLASS:
                        coupled_classes.add(edge.source)

        result[node.name] = len(coupled_classes)

    return result


def rfc(graph: SemGraph) -> dict[str, int]:
    """Response For a Class: methods in class + distinct methods directly called by them."""
    result: dict[str, int] = {}

    for node in graph.all_nodes(type=NodeType.CLASS):
        methods = _class_methods(graph, node.name)
        callees: set[str] = set()
        for method in methods:
            for edge in graph.outgoing(method, rel=RelType.CALLS):
                callees.add(edge.target)
        result[node.name] = len(methods) + len(callees)

    return result


def lcom4(graph: SemGraph) -> dict[str, int]:
    """Lack of Cohesion in Methods (variant 4).

    Number of connected components in the intra-class method interaction graph.
    Methods are connected if one calls the other (within the same class).
    LCOM4=1 means fully cohesive. LCOM4>1 means the class has disjoint responsibilities.
    """
    result: dict[str, int] = {}

    for node in graph.all_nodes(type=NodeType.CLASS):
        methods = _class_methods(graph, node.name)
        if not methods:
            result[node.name] = 0
            continue

        method_set = set(methods)
        # Build undirected adjacency among methods (connected if one calls the other)
        adj: dict[str, set[str]] = {m: set() for m in methods}
        for method in methods:
            for edge in graph.outgoing(method, rel=RelType.CALLS):
                if edge.target in method_set:
                    adj[method].add(edge.target)
                    adj[edge.target].add(method)

        # Count connected components via BFS
        visited: set[str] = set()
        components = 0
        for method in methods:
            if method in visited:
                continue
            components += 1
            queue = [method]
            while queue:
                current = queue.pop()
                if current in visited:
                    continue
                visited.add(current)
                for neighbor in adj[current]:
                    if neighbor not in visited:
                        queue.append(neighbor)

        result[node.name] = components

    return result


# --- Martin's Package Metrics ---


def martin_metrics(graph: SemGraph) -> dict[str, dict]:
    """Compute Martin's package metrics for each module/package.

    Returns dict keyed by module name with values:
        ca: afferent coupling (incoming inter-module deps)
        ce: efferent coupling (outgoing inter-module deps)
        instability: Ce / (Ca + Ce)
        abstractness: abstract classes / total classes
        distance: |A + I - 1|
    """
    result: dict[str, dict] = {}

    # Build module -> members mapping
    modules: list[str] = []
    module_members: dict[str, set[str]] = {}
    for node in graph.all_nodes():
        if node.type.value in ("module", "package"):
            modules.append(node.name)
            module_members[node.name] = _module_members(graph, node.name)

    # Build node -> module mapping
    node_to_module: dict[str, str] = {}
    for mod, members in module_members.items():
        for member in members:
            node_to_module[member] = mod
        node_to_module[mod] = mod

    for mod in modules:
        members = module_members[mod]
        all_in_module = members | {mod}

        # Ca: distinct external modules that depend on this module
        ca_modules: set[str] = set()
        for member in all_in_module:
            for edge in graph.incoming(member):
                if edge.rel.value in _COUPLING_RELS:
                    source_mod = node_to_module.get(edge.source)
                    if source_mod and source_mod != mod:
                        ca_modules.add(source_mod)

        # Ce: distinct external modules this module depends on
        ce_modules: set[str] = set()
        for member in all_in_module:
            for edge in graph.outgoing(member):
                if edge.rel.value in _COUPLING_RELS:
                    target_mod = node_to_module.get(edge.target)
                    if target_mod and target_mod != mod:
                        ce_modules.add(target_mod)

        ca = len(ca_modules)
        ce = len(ce_modules)
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.0

        # Abstractness: ratio of interfaces/abstract classes to total classes
        classes_in_mod = [m for m in members if graph.get_node(m) and graph.get_node(m).type.value in ("class", "interface")]
        abstract_count = sum(1 for m in classes_in_mod if graph.get_node(m).type == NodeType.INTERFACE)
        total_classes = len(classes_in_mod)
        abstractness = abstract_count / total_classes if total_classes > 0 else 0.0

        distance = abs(abstractness + instability - 1.0)

        result[mod] = {
            "ca": ca,
            "ce": ce,
            "instability": round(instability, 3),
            "abstractness": round(abstractness, 3),
            "distance": round(distance, 3),
        }

    return result


def sdp_violations(graph: SemGraph) -> list[dict]:
    """Find Stable Dependencies Principle violations.

    A violation occurs when a stable module (low instability) depends on
    an unstable module (high instability). Dependencies should flow toward
    stability.
    """
    metrics = martin_metrics(graph)
    violations: list[dict] = []

    for edge in graph.all_edges():
        if edge.rel.value in (RelType.IMPORTS.value, RelType.DEPENDS_ON.value):
            source_metrics = metrics.get(edge.source)
            target_metrics = metrics.get(edge.target)
            if source_metrics and target_metrics:
                si = source_metrics["instability"]
                ti = target_metrics["instability"]
                # Violation: source is more stable than target
                if si < ti and (ti - si) > 0.1:  # threshold to avoid noise
                    violations.append({
                        "source": edge.source,
                        "target": edge.target,
                        "source_instability": si,
                        "target_instability": ti,
                    })

    return sorted(violations, key=lambda v: v["target_instability"] - v["source_instability"], reverse=True)


# --- Code Smell Detection ---


def god_classes(
    graph: SemGraph,
    wmc_threshold: int = 20,
    cbo_threshold: int = 5,
    lcom_threshold: int = 2,
) -> list[dict]:
    """Detect God Classes: high complexity AND high coupling AND low cohesion.

    A God Class has too many responsibilities. Detected when ALL of:
    - WMC (weighted methods) >= wmc_threshold
    - CBO (coupling to other classes) >= cbo_threshold
    - LCOM4 (connected components) >= lcom_threshold

    Returns list of dicts with name and metric values.
    """
    wmc_data = wmc(graph)
    cbo_data = cbo(graph)
    lcom_data = lcom4(graph)

    results: list[dict] = []
    for name in wmc_data:
        w = wmc_data.get(name, 0)
        c = cbo_data.get(name, 0)
        l = lcom_data.get(name, 0)
        if w >= wmc_threshold and c >= cbo_threshold and l >= lcom_threshold:
            results.append({
                "name": name,
                "wmc": w,
                "cbo": c,
                "lcom4": l,
            })

    return sorted(results, key=lambda r: r["wmc"], reverse=True)


def feature_envy(graph: SemGraph) -> list[dict]:
    """Detect Feature Envy: methods that reference more members of another class than their own.

    A method has Feature Envy when it makes more calls to methods/fields
    of some external class than to members of its own class. This suggests
    the method may belong in the other class.

    Returns list of dicts with method, own_class, envied_class, own_refs, envied_refs.
    """
    results: list[dict] = []

    for node in graph.all_nodes(type=NodeType.CLASS):
        own_members = set(_class_members(graph, node.name))
        methods = _class_methods(graph, node.name)

        for method in methods:
            # Count outgoing coupling edges by target class
            own_refs = 0
            external_refs: dict[str, int] = defaultdict(int)

            for edge in graph.outgoing(method):
                if edge.rel.value not in _COUPLING_RELS:
                    continue
                if edge.target in own_members or edge.target == node.name:
                    own_refs += 1
                else:
                    target_class = _containing_class(graph, edge.target)
                    if target_class and target_class != node.name:
                        external_refs[target_class] += 1

            if not external_refs:
                continue

            # Find the most-referenced external class
            envied, envied_count = max(external_refs.items(), key=lambda x: x[1])
            if envied_count > own_refs and envied_count >= 2:
                results.append({
                    "method": method,
                    "own_class": node.name,
                    "envied_class": envied,
                    "own_refs": own_refs,
                    "envied_refs": envied_count,
                })

    return sorted(results, key=lambda r: r["envied_refs"] - r["own_refs"], reverse=True)


def shotgun_surgery(
    graph: SemGraph,
    fan_out_threshold: int = 7,
) -> list[dict]:
    """Detect Shotgun Surgery: functions/methods with high coupling fan-out.

    A change to a node with high fan-out likely requires coordinated
    changes across many other nodes. This makes the code fragile.

    Returns list of dicts with name, type, fan_out, targets.
    """
    results: list[dict] = []

    for node in graph.all_nodes():
        if node.type.value not in ("function", "method"):
            continue

        targets: set[str] = set()
        for edge in graph.outgoing(node.name):
            if edge.rel.value in _COUPLING_RELS:
                targets.add(edge.target)

        if len(targets) >= fan_out_threshold:
            results.append({
                "name": node.name,
                "type": node.type.value,
                "fan_out": len(targets),
                "targets": sorted(targets),
            })

    return sorted(results, key=lambda r: r["fan_out"], reverse=True)
