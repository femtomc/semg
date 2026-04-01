from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Node as TSNode, Parser

from semg.langs import ExtractResult, register
from semg.metrics import PYTHON_BRANCH_MAP, compute_metrics
from semg.model import Edge, Node, NodeType, RelType

_LANGUAGE = Language(tspython.language())
_PARSER = Parser(_LANGUAGE)

# Common builtins to skip (these never resolve to graph nodes)
_BUILTINS = frozenset({
    "print", "len", "range", "enumerate", "zip", "map", "filter",
    "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
    "type", "id", "hash", "repr", "str", "int", "float", "bool", "bytes",
    "list", "dict", "set", "tuple", "frozenset",
    "sorted", "reversed", "min", "max", "sum", "abs", "round",
    "open", "iter", "next", "any", "all",
    "super", "property", "staticmethod", "classmethod",
    "ValueError", "TypeError", "KeyError", "AttributeError", "RuntimeError",
    "Exception", "NotImplementedError", "StopIteration", "AssertionError",
    "OSError", "IOError", "FileNotFoundError", "ImportError",
})


class PythonExtractor:
    extensions = [".py"]
    branch_map = PYTHON_BRANCH_MAP

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        tree = _PARSER.parse(source)
        nodes: list[Node] = []
        edges: list[Edge] = []
        self._walk_body(tree.root_node, module_name, file_path, nodes, edges)
        self._extract_imports(tree.root_node, module_name, edges)
        return ExtractResult(nodes=nodes, edges=edges)

    def _walk_body(
        self,
        body_node: TSNode,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Walk children of a block/module, extracting classes, functions, and assignments."""
        for child in body_node.children:
            if child.type == "class_definition":
                self._extract_class(child, parent_name, file_path, nodes, edges)
            elif child.type == "function_definition":
                self._extract_function(child, parent_name, file_path, nodes, edges)
            elif child.type == "decorated_definition":
                # Unwrap to the inner definition
                decorators = [c for c in child.children if c.type == "decorator"]
                inner = child.child_by_field_name("definition")
                if inner is not None and inner.type == "class_definition":
                    self._extract_class(inner, parent_name, file_path, nodes, edges, decorators)
                elif inner is not None and inner.type == "function_definition":
                    self._extract_function(inner, parent_name, file_path, nodes, edges, decorators)
            elif child.type == "expression_statement":
                self._extract_assignment(child, parent_name, file_path, nodes, edges)

    def _extract_class(
        self,
        node: TSNode,
        parent_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
        decorators: list[TSNode] | None = None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        class_name = name_node.text.decode()
        qualified = f"{parent_name}.{class_name}"

        out_nodes.append(Node(
            name=qualified,
            type=NodeType.CLASS,
            file=file_path,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            docstring=self._get_docstring(node),
        ))
        out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Inheritance
        superclasses = node.child_by_field_name("superclasses")
        if superclasses is not None:
            for arg in superclasses.children:
                if arg.type == "identifier":
                    base_name = arg.text.decode()
                    out_edges.append(Edge(
                        source=qualified,
                        target=base_name,
                        rel=RelType.INHERITS,
                        metadata={"unresolved": True},
                    ))
                elif arg.type == "attribute":
                    base_name = arg.text.decode()
                    out_edges.append(Edge(
                        source=qualified,
                        target=base_name,
                        rel=RelType.INHERITS,
                        metadata={"unresolved": True},
                    ))

        # Decorators
        if decorators:
            for dec in decorators:
                dec_name = self._decorator_name(dec)
                if dec_name:
                    out_edges.append(Edge(
                        source=dec_name,
                        target=qualified,
                        rel=RelType.DECORATES,
                        metadata={"unresolved": True},
                    ))

        # Walk class body for methods and nested classes
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_body(body, qualified, file_path, out_nodes, out_edges)

    def _extract_function(
        self,
        node: TSNode,
        parent_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
        decorators: list[TSNode] | None = None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        func_name = name_node.text.decode()
        qualified = f"{parent_name}.{func_name}"

        # Determine if this is a method (parent is a class) or a function
        # We check by looking at whether the parent_name corresponds to a class
        # by checking the parameters for 'self' or 'cls'
        params = node.child_by_field_name("parameters")
        is_method = self._has_self_or_cls(params)

        # Compute AST metrics
        metrics = compute_metrics(node, self.branch_map)

        out_nodes.append(Node(
            name=qualified,
            type=NodeType.METHOD if is_method else NodeType.FUNCTION,
            file=file_path,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            docstring=self._get_docstring(node),
            metadata={"metrics": metrics.to_dict()},
        ))
        out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Decorators
        if decorators:
            for dec in decorators:
                dec_name = self._decorator_name(dec)
                if dec_name:
                    out_edges.append(Edge(
                        source=dec_name,
                        target=qualified,
                        rel=RelType.DECORATES,
                        metadata={"unresolved": True},
                    ))

        # Extract calls from function body
        body = node.child_by_field_name("body")
        if body is not None:
            class_name = parent_name if is_method else None
            self._extract_calls(body, qualified, class_name, out_edges)

    def _extract_calls(
        self,
        node: TSNode,
        caller_name: str,
        class_name: str | None,
        out_edges: list[Edge],
    ) -> None:
        """Recursively walk AST and extract call edges."""
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                result = self._call_target(func_node, class_name)
                if result is not None:
                    target, resolved = result
                    metadata = {} if resolved else {"unresolved": True}
                    out_edges.append(Edge(
                        source=caller_name,
                        target=target,
                        rel=RelType.CALLS,
                        metadata=metadata,
                    ))
        # Recurse into children (but not into nested function/class definitions)
        for child in node.children:
            if child.type in ("function_definition", "class_definition", "decorated_definition"):
                continue
            self._extract_calls(child, caller_name, class_name, out_edges)

    def _call_target(self, func_node: TSNode, class_name: str | None) -> tuple[str, bool] | None:
        """Resolve a call's function node to (target_name, is_resolved).

        Returns None to skip the call (dynamic/unparseable).
        """
        if func_node.type == "identifier":
            name = func_node.text.decode()
            # Skip common builtins
            if name in _BUILTINS:
                return None
            return (name, False)  # unresolved — needs suffix matching

        if func_node.type == "attribute":
            obj = func_node.child_by_field_name("object")
            attr = func_node.child_by_field_name("attribute")
            if obj is None or attr is None:
                return None
            attr_name = attr.text.decode()

            # self.method() or cls.method() — resolve to ClassName.method
            if obj.type == "identifier" and obj.text in (b"self", b"cls") and class_name:
                return (f"{class_name}.{attr_name}", True)  # resolved

            # super().method() — skip for now
            if obj.type == "call":
                inner = obj.child_by_field_name("function")
                if inner and inner.type == "identifier" and inner.text == b"super":
                    return None

            # obj.method() — try as dotted name, unresolved
            if obj.type == "identifier":
                obj_name = obj.text.decode()
                return (f"{obj_name}.{attr_name}", False)

            # module.sub.func() — full dotted name
            if obj.type == "attribute":
                return (f"{obj.text.decode()}.{attr_name}", False)

        return None

    def _extract_assignment(
        self,
        node: TSNode,
        parent_name: str,
        file_path: str,
        out_nodes: list[Node],
        out_edges: list[Edge],
    ) -> None:
        """Extract module-level or class-level variable assignments."""
        # Only extract UPPERCASE assignments as constants, others skip
        for child in node.children:
            if child.type != "assignment":
                continue
            left = child.child_by_field_name("left")
            if left is None or left.type != "identifier":
                continue
            var_name = left.text.decode()
            if not var_name.isupper():
                continue
            qualified = f"{parent_name}.{var_name}"
            out_nodes.append(Node(
                name=qualified,
                type=NodeType.CONSTANT,
                file=file_path,
                line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
            ))
            out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_imports(
        self,
        root: TSNode,
        module_name: str,
        out_edges: list[Edge],
    ) -> None:
        """Extract import statements as IMPORTS edges."""
        for child in root.children:
            if child.type == "import_statement":
                # import X, import X.Y
                for name_node in child.children:
                    if name_node.type == "dotted_name":
                        target = name_node.text.decode()
                        out_edges.append(Edge(
                            source=module_name,
                            target=target,
                            rel=RelType.IMPORTS,
                            metadata={"unresolved": True},
                        ))
            elif child.type == "import_from_statement":
                # from X import Y — handles both absolute and relative imports
                target = self._resolve_import_from(child, module_name)
                if target is not None:
                    out_edges.append(Edge(
                        source=module_name,
                        target=target,
                        rel=RelType.IMPORTS,
                        metadata={"unresolved": True},
                    ))
            elif child.type == "future_import_statement":
                pass  # skip `from __future__ import ...`

    def _resolve_import_from(self, node: TSNode, module_name: str) -> str | None:
        """Resolve a from...import statement to a target module name.

        Handles:
          from X.Y import Z        -> "X.Y"
          from .sibling import Z   -> "parent.sibling" (relative)
          from ..uncle import Z    -> "grandparent.uncle" (relative)
          from . import Z          -> "parent" (relative, package itself)
        """
        # Check for relative import first
        for child in node.children:
            if child.type == "relative_import":
                return self._resolve_relative_import(child, module_name)

        # Absolute import: from X.Y import Z
        mod_node = node.child_by_field_name("module_name")
        if mod_node is not None:
            return mod_node.text.decode()
        return None

    def _resolve_relative_import(self, rel_node: TSNode, module_name: str) -> str | None:
        """Resolve a relative_import node using the current module name.

        relative_import has:
          import_prefix (the dots: ".", "..", etc.)
          dotted_name (optional: the module after the dots)
        """
        prefix_node = None
        dotted_node = None
        for child in rel_node.children:
            if child.type == "import_prefix":
                prefix_node = child
            elif child.type == "dotted_name":
                dotted_node = child

        # Count dots for relative depth
        dot_count = len(prefix_node.text.decode()) if prefix_node else 0
        if dot_count == 0:
            return None

        # Walk up the module path: "bellman.cli.commands" with 1 dot -> "bellman.cli"
        parts = module_name.split(".")
        if dot_count > len(parts):
            return None  # too many dots, can't resolve

        base_parts = parts[:-dot_count]
        if dotted_node is not None:
            target = ".".join(base_parts + [dotted_node.text.decode()])
        else:
            # from . import X -> import from parent package
            target = ".".join(base_parts) if base_parts else None

        return target

    def _get_docstring(self, node: TSNode) -> str | None:
        """Extract docstring from a class or function definition."""
        body = node.child_by_field_name("body")
        if body is None or not body.children:
            return None
        first_stmt = body.children[0]
        if first_stmt.type != "expression_statement":
            return None
        expr = first_stmt.children[0] if first_stmt.children else None
        if expr is None or expr.type != "string":
            return None
        # Get the string content (strip quotes)
        content_node = next((c for c in expr.children if c.type == "string_content"), None)
        if content_node is not None:
            return content_node.text.decode().strip()
        return None

    def _has_self_or_cls(self, params: TSNode | None) -> bool:
        if params is None:
            return False
        for child in params.children:
            if child.type == "identifier" and child.text in (b"self", b"cls"):
                return True
        return False

    def _decorator_name(self, dec_node: TSNode) -> str | None:
        """Extract the name from a decorator node."""
        for child in dec_node.children:
            if child.type == "identifier":
                return child.text.decode()
            if child.type == "attribute":
                return child.text.decode()
            if child.type == "call":
                func = child.child_by_field_name("function")
                if func is not None:
                    return func.text.decode()
        return None


# Register on import
register(PythonExtractor())
