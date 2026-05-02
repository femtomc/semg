"""JavaScript and TypeScript extractor.

Handles .js, .jsx, .ts, .tsx files. The tree-sitter ASTs for JS and TS
are nearly identical — classes, functions, methods, calls, and imports
use the same node types. The main difference is TS has type annotations,
interface declarations, and uses type_identifier for class names.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from smg.hashing import content_hash, structure_hash
from smg.langs import ExtractResult, register
from smg.metrics import JS_BRANCH_MAP, compute_metrics_and_hash
from smg.model import Edge, Node, NodeType, RelType


def _node_text(node: TSNode) -> str:
    return (node.text or b"").decode()


# Common JS/TS builtins to skip
_BUILTINS = frozenset(
    {
        "console",
        "require",
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "Promise",
        "JSON",
        "Math",
        "Object",
        "Array",
        "String",
        "Number",
        "Boolean",
        "Date",
        "RegExp",
        "Error",
        "Map",
        "Set",
        "WeakMap",
        "WeakSet",
        "Symbol",
        "Proxy",
        "Reflect",
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "encodeURI",
        "decodeURI",
        "encodeURIComponent",
        "decodeURIComponent",
        "fetch",
        "alert",
        "confirm",
        "prompt",
        "TypeError",
        "RangeError",
        "ReferenceError",
        "SyntaxError",
    }
)


def _get_class_name(node: TSNode) -> str | None:
    """Extract class name — handles both identifier (JS) and type_identifier (TS)."""
    name = node.child_by_field_name("name")
    if name is not None:
        return _node_text(name)
    return None


class _JSExtractorBase:
    """Shared extraction logic for JS and TS."""

    def _extract(self, parser: Parser, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        tree = parser.parse(source)
        nodes: list[Node] = []
        edges: list[Edge] = []
        self._walk_body(tree.root_node, source, module_name, file_path, nodes, edges)
        self._extract_imports(tree.root_node, module_name, file_path, edges)
        return ExtractResult(nodes=nodes, edges=edges)

    def _walk_body(
        self,
        body_node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        for child in body_node.children:
            if child.type == "class_declaration":
                self._extract_class(child, source, parent_name, file_path, nodes, edges)
            elif child.type == "function_declaration":
                self._extract_function(child, source, parent_name, file_path, nodes, edges)
            elif child.type == "export_statement":
                # Unwrap: export class X {}, export function f() {}, export default ...
                for inner in child.children:
                    if inner.type == "class_declaration":
                        self._extract_class(inner, source, parent_name, file_path, nodes, edges)
                    elif inner.type == "function_declaration":
                        self._extract_function(inner, source, parent_name, file_path, nodes, edges)
                    elif inner.type in ("arrow_function", "function_expression"):
                        self._extract_function_node(inner, "default", source, parent_name, file_path, nodes, edges)
                    elif inner.type == "lexical_declaration":
                        self._extract_const(inner, source, parent_name, file_path, nodes, edges)
            elif child.type == "lexical_declaration":
                self._extract_const(child, source, parent_name, file_path, nodes, edges)
            elif child.type == "interface_declaration":
                self._extract_interface(child, parent_name, file_path, nodes, edges)

    def _extract_class(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
    ) -> None:
        class_name = _get_class_name(node)
        if class_name is None:
            return
        qualified = f"{parent_name}.{class_name}"

        out_nodes.append(
            Node(
                name=qualified,
                type=NodeType.CLASS,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                docstring=self._get_jsdoc(node),
                metadata={
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": structure_hash(node),
                },
            )
        )
        out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Inheritance: extends clause
        heritage = node.child_by_field_name("heritage") or _find_child(node, "class_heritage")
        if heritage is not None:
            for child in heritage.children:
                if child.type == "extends_clause":
                    for ident in child.children:
                        if ident.type in ("identifier", "type_identifier"):
                            out_edges.append(
                                Edge(
                                    source=qualified,
                                    target=_node_text(ident),
                                    rel=RelType.INHERITS,
                                    metadata={"unresolved": True},
                                )
                            )
                elif child.type == "implements_clause":
                    for ident in child.children:
                        if ident.type in ("identifier", "type_identifier"):
                            out_edges.append(
                                Edge(
                                    source=qualified,
                                    target=_node_text(ident),
                                    rel=RelType.IMPLEMENTS,
                                    metadata={"unresolved": True},
                                )
                            )

        # Walk class body for methods
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                if child.type == "method_definition":
                    self._extract_method(child, source, qualified, file_path, out_nodes, out_edges)

    def _extract_method(
        self,
        node: TSNode,
        source: bytes,
        class_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        method_name = _node_text(name_node)
        qualified = f"{class_name}.{method_name}"

        meta = compute_metrics_and_hash(node, JS_BRANCH_MAP)

        out_nodes.append(
            Node(
                name=qualified,
                type=NodeType.METHOD,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                docstring=self._get_jsdoc(node),
                metadata={
                    "metrics": meta.metrics.to_dict(),
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": meta.structure_hash,
                },
            )
        )
        out_edges.append(Edge(source=class_name, target=qualified, rel=RelType.CONTAINS))

        # Extract calls from method body
        body = node.child_by_field_name("body")
        if body is not None:
            self._extract_calls(body, qualified, class_name, out_edges)

    def _extract_function(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        func_name = _node_text(name_node)
        self._extract_function_node(node, func_name, source, parent_name, file_path, out_nodes, out_edges)

    def _extract_function_node(
        self,
        node: TSNode,
        func_name: str,
        source: bytes,
        parent_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
    ) -> None:
        qualified = f"{parent_name}.{func_name}"

        meta = compute_metrics_and_hash(node, JS_BRANCH_MAP)

        out_nodes.append(
            Node(
                name=qualified,
                type=NodeType.FUNCTION,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                docstring=self._get_jsdoc(node),
                metadata={
                    "metrics": meta.metrics.to_dict(),
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": meta.structure_hash,
                },
            )
        )
        out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Extract calls from function body
        body = node.child_by_field_name("body")
        if body is not None:
            self._extract_calls(body, qualified, None, out_edges)

    def _extract_const(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
    ) -> None:
        """Extract const/let declarations for function values and UPPER_CASE constants."""
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            if name_node is None or name_node.type != "identifier":
                continue
            var_name = _node_text(name_node)

            value = child.child_by_field_name("value")
            if value is not None and value.type in ("arrow_function", "function_expression"):
                self._extract_function_node(value, var_name, source, parent_name, file_path, out_nodes, out_edges)
                continue

            if not var_name.isupper():
                continue
            qualified = f"{parent_name}.{var_name}"
            out_nodes.append(
                Node(
                    name=qualified,
                    type=NodeType.CONSTANT,
                    file=file_path,
                    line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                )
            )
            out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_interface(
        self,
        node: TSNode,
        parent_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
    ) -> None:
        """Extract TypeScript interface declarations."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        iface_name = _node_text(name_node)
        qualified = f"{parent_name}.{iface_name}"
        out_nodes.append(
            Node(
                name=qualified,
                type=NodeType.INTERFACE,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
            )
        )
        out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_imports(
        self,
        root: TSNode,
        module_name: str,
        file_path: str,
        out_edges: list[Edge],
    ) -> None:
        """Extract import statements as IMPORTS edges."""
        for child in root.children:
            if child.type == "import_statement":
                # import X from "source" / import { X } from "source"
                source = child.child_by_field_name("source")
                if source is not None:
                    target = self._import_source_to_module(source, module_name, file_path)
                    if target:
                        out_edges.append(
                            Edge(
                                source=module_name,
                                target=target,
                                rel=RelType.IMPORTS,
                                metadata={"unresolved": True},
                            )
                        )

    def _import_source_to_module(
        self,
        source_node: TSNode,
        module_name: str,
        file_path: str,
    ) -> str | None:
        """Convert an import source string node to a module name.

        Relative imports are resolved against the importing module.
        Bare package imports are normalized from npm notation to dotted names.
        """
        # Get the string content
        for child in source_node.children:
            if child.type == "string_fragment":
                raw = _node_text(child)
                path = self._strip_import_extension(raw)
                if not path:
                    return None
                if path.startswith("."):
                    return self._resolve_relative_import(path, module_name, file_path)
                return self._normalize_import_path(path)
        return None

    def _strip_import_extension(self, path: str) -> str:
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            if path.endswith(ext):
                return path[: -len(ext)]
        return path

    def _normalize_import_path(self, path: str) -> str | None:
        cleaned = path[1:] if path.startswith("@") else path
        parts = [part for part in cleaned.split("/") if part not in ("", ".")]
        if not parts:
            return None
        if parts[-1] == "index":
            parts.pop()
        return ".".join(parts) if parts else None

    def _resolve_relative_import(self, path: str, module_name: str, file_path: str) -> str | None:
        base_parts = module_name.split(".")
        if Path(file_path).stem not in ("index", "__init__"):
            base_parts = base_parts[:-1]

        parts = [part for part in path.split("/") if part]
        resolved = list(base_parts)
        for part in parts:
            if part == ".":
                continue
            if part == "..":
                if not resolved:
                    return None
                resolved.pop()
                continue
            resolved.append(part)

        if resolved and resolved[-1] == "index":
            resolved.pop()
        return ".".join(resolved) if resolved else None

    def _extract_calls(
        self,
        root: TSNode,
        caller_name: str,
        class_name: str | None,
        out_edges: list[Edge],
    ) -> None:
        """Iteratively walk AST and extract call edges."""
        _skip = frozenset({"function_declaration", "class_declaration", "arrow_function"})
        stack: list[TSNode] = [root]
        while stack:
            node = stack.pop()
            if node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node is not None:
                    result = self._call_target(func_node, class_name)
                    if result is not None:
                        target, resolved = result
                        metadata = {} if resolved else {"unresolved": True}
                        out_edges.append(
                            Edge(
                                source=caller_name,
                                target=target,
                                rel=RelType.CALLS,
                                metadata=metadata,
                            )
                        )
            for child in node.children:
                if child.type not in _skip:
                    stack.append(child)

    def _call_target(self, func_node: TSNode, class_name: str | None) -> tuple[str, bool] | None:
        """Resolve a call's function node to (target_name, is_resolved)."""
        if func_node.type == "identifier":
            name = _node_text(func_node)
            if name in _BUILTINS:
                return None
            return (name, False)

        if func_node.type == "member_expression":
            obj = func_node.child_by_field_name("object")
            prop = func_node.child_by_field_name("property")
            if obj is None or prop is None:
                return None
            prop_name = _node_text(prop)

            # this.method() -> ClassName.method
            if obj.type == "this" and class_name:
                return (f"{class_name}.{prop_name}", True)

            # super.method() -> skip
            if obj.type == "super":
                return None

            # console.log, etc — skip known builtins
            if obj.type == "identifier" and _node_text(obj) in _BUILTINS:
                return None

            # obj.method() -> "obj.method" (unresolved)
            if obj.type == "identifier":
                return (f"{_node_text(obj)}.{prop_name}", False)

            # deeper member_expression: a.b.c() -> "a.b.c"
            if obj.type == "member_expression":
                return (f"{_node_text(obj)}.{prop_name}", False)

        return None

    def _get_jsdoc(self, node: TSNode) -> str | None:
        """Extract JSDoc comment preceding a node."""
        # Look for a comment sibling immediately before this node
        prev = node.prev_named_sibling
        if prev is not None and prev.type == "comment":
            text = _node_text(prev)
            # JSDoc: /** ... */
            if text.startswith("/**"):
                # Extract first line of content
                lines = text.strip("/* \n").split("\n")
                for line in lines:
                    line = line.strip().lstrip("* ").strip()
                    if line:
                        return line
        return None


# --- Concrete extractors ---


class JavaScriptExtractor(_JSExtractorBase):
    extensions = [".js", ".jsx", ".mjs", ".cjs"]
    branch_map = JS_BRANCH_MAP

    def __init__(self) -> None:
        import tree_sitter_javascript as tsjs

        self._parser = Parser(Language(tsjs.language()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        return self._extract(self._parser, source, file_path, module_name)


class TypeScriptExtractor(_JSExtractorBase):
    extensions = [".ts"]
    branch_map = JS_BRANCH_MAP

    def __init__(self) -> None:
        import tree_sitter_typescript as tsts

        self._parser = Parser(Language(tsts.language_typescript()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        return self._extract(self._parser, source, file_path, module_name)


class TSXExtractor(_JSExtractorBase):
    extensions = [".tsx"]
    branch_map = JS_BRANCH_MAP

    def __init__(self) -> None:
        import tree_sitter_typescript as tsts

        self._parser = Parser(Language(tsts.language_tsx()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        return self._extract(self._parser, source, file_path, module_name)


def _find_child(node: TSNode, type_name: str) -> TSNode | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


# Register all extractors — each catches its own ImportError
try:
    register(JavaScriptExtractor())
except ImportError:
    pass

try:
    register(TypeScriptExtractor())
    register(TSXExtractor())
except ImportError:
    pass
