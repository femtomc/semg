from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from smg.hashing import content_hash, structure_hash
from smg.langs import ExtractResult, register
from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics_and_hash
from smg.model import Edge, Node, NodeType, RelType

_LANGUAGE = Language(tspython.language())
_PARSER = Parser(_LANGUAGE)

# Common builtins to skip (these never resolve to graph nodes)
_BUILTINS = frozenset(
    {
        "print",
        "len",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "isinstance",
        "issubclass",
        "hasattr",
        "getattr",
        "setattr",
        "delattr",
        "type",
        "id",
        "hash",
        "repr",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "list",
        "dict",
        "set",
        "tuple",
        "frozenset",
        "sorted",
        "reversed",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "open",
        "iter",
        "next",
        "any",
        "all",
        "super",
        "property",
        "staticmethod",
        "classmethod",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "RuntimeError",
        "Exception",
        "NotImplementedError",
        "StopIteration",
        "AssertionError",
        "OSError",
        "IOError",
        "FileNotFoundError",
        "ImportError",
    }
)


class PythonExtractor:
    extensions = [".py"]
    branch_map = PYTHON_BRANCH_MAP

    # Set to True to use the native Zig extractor (prototype — faster but
    # not yet feature-complete; missing: docstrings, relative imports,
    # attribute chain calls). Enable for benchmarking or when these features
    # aren't needed.
    use_native: bool = False

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        if self.use_native:
            try:
                from smg._accel import extract_python_native

                result = extract_python_native(source, file_path, module_name)
                if result is not None:
                    return _convert_native_result(result)
            except Exception:
                pass

        # Python extraction (full-featured)
        tree = _PARSER.parse(source)
        nodes: list[Node] = []
        edges: list[Edge] = []
        self._walk_body(tree.root_node, source, module_name, file_path, nodes, edges)
        self._extract_imports(tree.root_node, module_name, edges)
        self._extract_dynamic_imports(tree.root_node, module_name, edges)
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
        """Walk children of a block/module, extracting classes, functions, and assignments."""
        for child in body_node.children:
            if child.type == "class_definition":
                self._extract_class(child, source, parent_name, file_path, nodes, edges)
            elif child.type == "function_definition":
                self._extract_function(child, source, parent_name, file_path, nodes, edges)
            elif child.type == "decorated_definition":
                # Unwrap to the inner definition
                decorators = [c for c in child.children if c.type == "decorator"]
                inner = child.child_by_field_name("definition")
                if inner is not None and inner.type == "class_definition":
                    self._extract_class(inner, source, parent_name, file_path, nodes, edges, decorators)
                elif inner is not None and inner.type == "function_definition":
                    self._extract_function(inner, source, parent_name, file_path, nodes, edges, decorators)
            elif child.type == "expression_statement":
                self._extract_assignment(child, parent_name, file_path, nodes, edges)

    def _extract_class(
        self,
        node: TSNode,
        source: bytes,
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

        out_nodes.append(
            Node(
                name=qualified,
                type=NodeType.CLASS,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                docstring=self._get_docstring(node),
                metadata={
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": structure_hash(node),
                },
            )
        )
        out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Inheritance
        superclasses = node.child_by_field_name("superclasses")
        if superclasses is not None:
            for arg in superclasses.children:
                if arg.type == "identifier":
                    base_name = arg.text.decode()
                    out_edges.append(
                        Edge(
                            source=qualified,
                            target=base_name,
                            rel=RelType.INHERITS,
                            metadata={"unresolved": True},
                        )
                    )
                elif arg.type == "attribute":
                    base_name = arg.text.decode()
                    out_edges.append(
                        Edge(
                            source=qualified,
                            target=base_name,
                            rel=RelType.INHERITS,
                            metadata={"unresolved": True},
                        )
                    )

        # Decorators
        if decorators:
            for dec in decorators:
                dec_name = self._decorator_name(dec)
                if dec_name:
                    out_edges.append(
                        Edge(
                            source=dec_name,
                            target=qualified,
                            rel=RelType.DECORATES,
                            metadata={"unresolved": True},
                        )
                    )

        # Walk class body for methods and nested classes
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_body(body, source, qualified, file_path, out_nodes, out_edges)

    def _extract_function(
        self,
        node: TSNode,
        source: bytes,
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

        # Fused metrics + structure hash in a single AST walk
        meta = compute_metrics_and_hash(node, self.branch_map)

        out_nodes.append(
            Node(
                name=qualified,
                type=NodeType.METHOD if is_method else NodeType.FUNCTION,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                docstring=self._get_docstring(node),
                metadata={
                    "metrics": meta.metrics.to_dict(),
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": meta.structure_hash,
                },
            )
        )
        out_edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Decorators
        if decorators:
            for dec in decorators:
                dec_name = self._decorator_name(dec)
                if dec_name:
                    out_edges.append(
                        Edge(
                            source=dec_name,
                            target=qualified,
                            rel=RelType.DECORATES,
                            metadata={"unresolved": True},
                        )
                    )

        # Extract calls from function body
        body = node.child_by_field_name("body")
        if body is not None:
            class_name = parent_name if is_method else None
            self._extract_calls(body, qualified, class_name, out_edges)

    def _extract_calls(
        self,
        root: TSNode,
        caller_name: str,
        class_name: str | None,
        out_edges: list[Edge],
    ) -> None:
        """Iteratively walk AST and extract call edges."""
        _skip = frozenset({"function_definition", "class_definition", "decorated_definition"})
        stack: list[TSNode] = [root]
        while stack:
            node = stack.pop()
            if node.type == "call":
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

    def _extract_imports(
        self,
        root: TSNode,
        module_name: str,
        out_edges: list[Edge],
    ) -> None:
        """Extract import statements as IMPORTS edges via full AST walk."""
        root_children = frozenset(id(c) for c in root.children)
        stack: list[TSNode] = [root]
        while stack:
            node = stack.pop()
            if node.type == "import_statement":
                deferred = id(node) not in root_children
                for name_node in node.children:
                    if name_node.type == "dotted_name":
                        target = name_node.text.decode()
                        meta: dict = {"unresolved": True}
                        if deferred:
                            meta["deferred"] = True
                        out_edges.append(
                            Edge(
                                source=module_name,
                                target=target,
                                rel=RelType.IMPORTS,
                                metadata=meta,
                            )
                        )
            elif node.type == "import_from_statement":
                deferred = id(node) not in root_children
                target = self._resolve_import_from(node, module_name)
                if target is not None:
                    meta = {"unresolved": True}
                    if deferred:
                        meta["deferred"] = True
                    out_edges.append(
                        Edge(
                            source=module_name,
                            target=target,
                            rel=RelType.IMPORTS,
                            metadata=meta,
                        )
                    )
            elif node.type == "future_import_statement":
                pass  # skip `from __future__ import ...`
            for child in node.children:
                stack.append(child)

    def _extract_dynamic_imports(
        self,
        root: TSNode,
        module_name: str,
        out_edges: list[Edge],
    ) -> None:
        """Extract importlib.import_module / __import__ calls as IMPORTS edges."""
        _DYNAMIC_IMPORT_FUNCS = {"importlib.import_module", "__import__"}
        stack: list[TSNode] = [root]
        while stack:
            node = stack.pop()
            if node.type == "call":
                func = node.child_by_field_name("function")
                if func is not None:
                    fname = func.text.decode()
                    if fname in _DYNAMIC_IMPORT_FUNCS:
                        args = node.child_by_field_name("arguments")
                        if args is not None:
                            first_arg = next(
                                (c for c in args.children if c.type == "string"),
                                None,
                            )
                            if first_arg is not None:
                                target = self._string_literal_value(first_arg)
                                if target and not target.startswith("."):
                                    out_edges.append(
                                        Edge(
                                            source=module_name,
                                            target=target,
                                            rel=RelType.IMPORTS,
                                            metadata={"dynamic": True, "unresolved": True},
                                        )
                                    )
            for child in node.children:
                stack.append(child)

    @staticmethod
    def _string_literal_value(string_node: TSNode) -> str | None:
        """Extract the text content of a tree-sitter string node."""
        content = next(
            (c for c in string_node.children if c.type == "string_content"),
            None,
        )
        return content.text.decode() if content is not None else None

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


def _convert_native_result(data: tuple[list, list]) -> ExtractResult:
    """Convert native JSONL extraction output to ExtractResult."""
    raw_nodes, raw_edges = data
    nodes: list[Node] = []
    edges: list[Edge] = []

    for r in raw_nodes:
        metadata: dict = {}
        if "cc" in r:
            metadata["metrics"] = {
                "cyclomatic_complexity": r["cc"],
                "cognitive_complexity": r.get("cog", 0),
                "max_nesting_depth": r.get("mnd", 0),
                "lines_of_code": r.get("loc", 0),
                "parameter_count": r.get("pc", 0),
                "return_count": r.get("rc", 0),
            }
        if "ch" in r:
            metadata["content_hash"] = r["ch"]
        if "sh" in r:
            metadata["structure_hash"] = r["sh"]

        nodes.append(
            Node(
                name=r["name"],
                type=NodeType(r["type"]),
                file=r.get("file"),
                line=r.get("line"),
                end_line=r.get("end_line"),
                docstring=r.get("doc"),
                metadata=metadata,
            )
        )

    for r in raw_edges:
        metadata = {}
        if r.get("unresolved"):
            metadata["unresolved"] = True
        edges.append(
            Edge(
                source=r["src"],
                target=r["tgt"],
                rel=RelType(r["rel"]),
                metadata=metadata,
            )
        )

    return ExtractResult(nodes=nodes, edges=edges)


# Register on import
register(PythonExtractor())
