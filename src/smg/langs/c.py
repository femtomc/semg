"""C and C++ extractor.

Handles .c, .h, .cpp, .hpp, .cc, .cxx, .hxx, .metal files.
C structs map to CLASS nodes, C++ classes map directly.
Metal Shading Language (.metal) is parsed as C++ since it shares the syntax.
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from smg.hashing import content_hash, structure_hash
from smg.langs import ExtractResult, register
from smg.metrics import BranchMap, compute_metrics_and_hash
from smg.model import Edge, Node, NodeType, RelType


def _node_text(node: TSNode) -> str:
    return (node.text or b"").decode()


_BUILTINS = frozenset(
    {
        # C standard library
        "printf",
        "fprintf",
        "sprintf",
        "snprintf",
        "scanf",
        "sscanf",
        "vprintf",
        "vfprintf",
        "vsprintf",
        "vsnprintf",
        "malloc",
        "calloc",
        "realloc",
        "free",
        "aligned_alloc",
        "memcpy",
        "memset",
        "memmove",
        "memcmp",
        "strlen",
        "strcpy",
        "strncpy",
        "strcmp",
        "strncmp",
        "strcat",
        "strncat",
        "strstr",
        "strchr",
        "strrchr",
        "strtok",
        "strtol",
        "strtoul",
        "strtod",
        "atoi",
        "atol",
        "atof",
        "sizeof",
        "assert",
        "exit",
        "abort",
        "atexit",
        "fopen",
        "fclose",
        "fread",
        "fwrite",
        "fgets",
        "fputs",
        "fseek",
        "ftell",
        "fflush",
        "feof",
        "ferror",
        "rewind",
        "remove",
        "rename",
        "tmpfile",
        "getchar",
        "putchar",
        "puts",
        "getline",
        "isalpha",
        "isdigit",
        "isalnum",
        "isspace",
        "isupper",
        "islower",
        "toupper",
        "tolower",
        "abs",
        "labs",
        "div",
        "ldiv",
        "qsort",
        "bsearch",
        "time",
        "clock",
        "difftime",
        "mktime",
        "strftime",
        "rand",
        "srand",
        "setjmp",
        "longjmp",
        "signal",
        "raise",
        "perror",
        "strerror",
        "errno",
        # C++ keywords / operators
        "static_cast",
        "dynamic_cast",
        "reinterpret_cast",
        "const_cast",
        "throw",
        "new",
        "delete",
        # C++ stdlib commonly called as bare identifiers
        "std",
        "cout",
        "cerr",
        "clog",
        "endl",
        "make_shared",
        "make_unique",
        "move",
        "forward",
        "swap",
        "begin",
        "end",
        "rbegin",
        "rend",
        "size",
        "empty",
        "push_back",
        "pop_back",
        "push_front",
        "pop_front",
        "emplace",
        "emplace_back",
        "insert",
        "erase",
        "clear",
        "find",
        "sort",
        "stable_sort",
        "lower_bound",
        "upper_bound",
        "min",
        "max",
        "clamp",
        "accumulate",
        "to_string",
        "stoi",
        "stol",
        "stoul",
        "stof",
        "stod",
        "get",
        "ref",
        "cref",
        # LLVM/compiler infrastructure common helpers
        "llvm_unreachable",
        "report_fatal_error",
        "isa",
        "cast",
        "dyn_cast",
        "dyn_cast_or_null",
        "cast_or_null",
        "dbgs",
        "errs",
        "outs",
    }
)

# Prefixes for identifiers that are almost certainly unresolvable
# (framework macros, compiler intrinsics, etc.)
_SKIP_PREFIXES = (
    "__builtin_",
    "__atomic_",
    "__sync_",
    "_mm",
    "__",
    "llvm_",
    "LLVM_",
    "NS_",
    "CF_",
    "CG_",  # Apple frameworks
)

_CPP_HEADER_MARKERS = (
    b"class ",
    b"namespace ",
    b"template ",
    b"public:",
    b"private:",
    b"protected:",
    b"virtual ",
    b"override",
    b"::",
)


@dataclass(frozen=True)
class _FunctionName:
    name: str
    qualifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ResolvedScope:
    containing_name: str
    class_name: str | None = None


@dataclass
class _CExtractionContext:
    is_cpp: bool
    namespaces: set[str]
    classes: set[str]
    types: set[str]
    function_macros: set[str]

    def resolve_qualified_scope(self, parent_name: str, qualifiers: tuple[str, ...]) -> _ResolvedScope:
        fallback = ".".join([parent_name, *qualifiers])
        for candidate in self._candidate_names(parent_name, qualifiers):
            if candidate in self.classes:
                return _ResolvedScope(containing_name=candidate, class_name=candidate)
            if candidate in self.namespaces:
                return _ResolvedScope(containing_name=candidate)
        return _ResolvedScope(containing_name=fallback)

    def resolve_type_name(self, parent_name: str, parts: list[str]) -> str:
        qualifiers = tuple(parts)
        for candidate in self._candidate_names(parent_name, qualifiers):
            if candidate in self.classes or candidate in self.types:
                return candidate
        return ".".join(parts)

    def _candidate_names(self, parent_name: str, parts: tuple[str, ...]) -> list[str]:
        if not parts:
            return []
        parent_parts = parent_name.split(".") if parent_name else []
        raw_candidates = [".".join([*parent_parts[:idx], *parts]) for idx in range(len(parent_parts), 0, -1)]
        raw_candidates.append(".".join(parts))
        seen: set[str] = set()
        candidates: list[str] = []
        for candidate in raw_candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
        return candidates


C_BRANCH_MAP = BranchMap(
    branch_nodes=frozenset(
        {
            "if_statement",
            "else_clause",
            "for_statement",
            "while_statement",
            "do_statement",
            "switch_statement",
            "case_statement",
            "conditional_expression",  # ternary
        }
    ),
    boolean_operators=frozenset({"binary_expression"}),
    nesting_nodes=frozenset(
        {
            "if_statement",
            "for_statement",
            "while_statement",
            "do_statement",
            "switch_statement",
        }
    ),
    loop_nodes=frozenset({"for_statement", "while_statement", "do_statement"}),
    function_nodes=frozenset({"function_definition"}),
    logical_operator_tokens=frozenset({"&&", "||"}),
)


class _CExtractorBase:
    """Shared extraction logic for C and C++."""

    def _extract(
        self,
        parser: Parser,
        source: bytes,
        file_path: str,
        module_name: str,
        is_cpp: bool,
    ) -> ExtractResult:
        tree = parser.parse(source)
        nodes: list[Node] = []
        edges: list[Edge] = []
        context = _collect_context(tree.root_node, module_name, is_cpp)
        self._walk_top_level(tree.root_node, source, module_name, file_path, nodes, edges, context)
        self._extract_includes(tree.root_node, module_name, edges)
        return ExtractResult(nodes=nodes, edges=edges)

    def _walk_top_level(
        self,
        root: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        context: _CExtractionContext,
    ) -> None:
        _TRANSPARENT = frozenset(
            {
                "preproc_ifdef",
                "preproc_if",
                "preproc_else",
                "preproc_elif",
                "linkage_specification",
                "declaration_list",
                "template_declaration",
            }
        )
        # Stack entries: (container_node, parent_name)
        stack: list[tuple[TSNode, str]] = [(root, parent_name)]
        while stack:
            container, pname = stack.pop()
            for child in container.children:
                ctype = child.type
                # Descend transparently into preprocessor conditionals / linkage specs
                if ctype in _TRANSPARENT:
                    stack.append((child, pname))
                    continue
                if ctype == "function_definition":
                    self._extract_function(child, source, pname, None, file_path, nodes, edges, context)
                elif ctype == "type_definition":
                    self._extract_typedef(child, source, pname, file_path, nodes, edges)
                elif ctype == "preproc_def":
                    self._extract_define(child, pname, file_path, nodes, edges)
                elif ctype == "preproc_function_def":
                    self._extract_function_macro(child, source, pname, file_path, nodes, edges)
                elif ctype == "declaration":
                    self._extract_declaration(child, source, pname, file_path, nodes, edges, context)
                elif ctype in ("struct_specifier", "union_specifier"):
                    if context.is_cpp:
                        self._extract_cpp_class(child, source, pname, file_path, nodes, edges, context)
                    else:
                        self._extract_record_type(child, source, pname, file_path, nodes, edges)
                elif ctype == "enum_specifier":
                    self._extract_enum_type(child, source, pname, file_path, nodes, edges)
                elif context.is_cpp and ctype == "namespace_definition":
                    self._extract_namespace(child, source, pname, file_path, nodes, edges, context)
                elif context.is_cpp and ctype == "class_specifier":
                    self._extract_cpp_class(child, source, pname, file_path, nodes, edges, context)

    def _extract_namespace(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        context: _CExtractionContext,
    ) -> None:
        name_node = _find_child(node, "namespace_identifier")
        if name_node is None:
            return
        ns_name = _node_text(name_node)
        qualified = f"{parent_name}.{ns_name}"

        nodes.append(
            Node(
                name=qualified,
                type=NodeType.PACKAGE,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
            )
        )
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        body = _find_child(node, "declaration_list")
        if body is not None:
            self._walk_top_level(body, source, qualified, file_path, nodes, edges, context)

    def _extract_declaration(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        context: _CExtractionContext,
    ) -> None:
        for inner in node.children:
            if inner.type in ("struct_specifier", "union_specifier"):
                if context.is_cpp:
                    self._extract_cpp_class(inner, source, parent_name, file_path, nodes, edges, context)
                else:
                    self._extract_record_type(inner, source, parent_name, file_path, nodes, edges)
            elif inner.type == "enum_specifier":
                self._extract_enum_type(inner, source, parent_name, file_path, nodes, edges)

    def _extract_cpp_class(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        context: _CExtractionContext,
    ) -> None:
        name_node = _find_child(node, "type_identifier")
        if name_node is None:
            return
        class_name = _node_text(name_node)
        qualified = f"{parent_name}.{class_name}"

        nodes.append(
            Node(
                name=qualified,
                type=NodeType.CLASS,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                metadata={
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": structure_hash(node),
                },
            )
        )
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Inheritance
        base_clause = _find_child(node, "base_class_clause")
        if base_clause is not None:
            for child in base_clause.children:
                if child.type == "access_specifier":
                    continue
                parts = _qualified_name_parts(child)
                if parts:
                    edges.append(
                        Edge(
                            source=qualified,
                            target=context.resolve_type_name(parent_name, parts),
                            rel=RelType.INHERITS,
                            metadata={"unresolved": True},
                        )
                    )

        # Methods in field_declaration_list
        body = _find_child(node, "field_declaration_list")
        if body is not None:
            for child in body.children:
                if child.type == "function_definition":
                    self._extract_function(child, source, qualified, qualified, file_path, nodes, edges, context)
                elif child.type in ("declaration", "field_declaration"):
                    self._extract_method_declaration(child, source, qualified, file_path, nodes, edges)

    def _extract_record_type(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        name_node = _find_child(node, "type_identifier")
        if name_node is None:
            return
        kind = "union" if node.type == "union_specifier" else "struct"
        qualified = f"{parent_name}.{_node_text(name_node)}"
        nodes.append(
            Node(
                name=qualified,
                type=NodeType.CLASS,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                metadata={
                    "c_kind": kind,
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": structure_hash(node),
                },
            )
        )
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_enum_type(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        name_node = _find_child(node, "type_identifier")
        if name_node is None:
            return
        qualified = f"{parent_name}.{_node_text(name_node)}"
        nodes.append(
            Node(
                name=qualified,
                type=NodeType.TYPE,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                metadata={
                    "c_kind": "enum",
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": structure_hash(node),
                },
            )
        )
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_method_declaration(
        self,
        node: TSNode,
        source: bytes,
        class_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        func_name = self._get_function_name(node)
        if func_name is None:
            return
        qualified = f"{class_name}.{func_name.name}"
        nodes.append(
            Node(
                name=qualified,
                type=NodeType.METHOD,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                metadata={
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": structure_hash(node),
                },
            )
        )
        edges.append(Edge(source=class_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_typedef(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract typedef struct/union/enum declarations as type nodes."""
        type_node = (
            _find_child(node, "struct_specifier")
            or _find_child(node, "union_specifier")
            or _find_child(node, "enum_specifier")
        )
        if type_node is None:
            return
        name_node = _find_child(node, "type_identifier")
        if name_node is None:
            return
        type_name = _node_text(name_node)
        qualified = f"{parent_name}.{type_name}"
        node_type = NodeType.TYPE if type_node.type == "enum_specifier" else NodeType.CLASS
        kind = {
            "struct_specifier": "struct",
            "union_specifier": "union",
            "enum_specifier": "enum",
        }[type_node.type]

        nodes.append(
            Node(
                name=qualified,
                type=node_type,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                metadata={
                    "c_kind": kind,
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": structure_hash(node),
                },
            )
        )
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_define(
        self,
        node: TSNode,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract #define NAME as CONSTANT."""
        name_node = _find_child(node, "identifier")
        if name_node is None:
            return
        name = _node_text(name_node)
        if not name.isupper():
            return
        qualified = f"{parent_name}.{name}"
        nodes.append(
            Node(
                name=qualified,
                type=NodeType.CONSTANT,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
            )
        )
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_function_macro(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        name_node = node.child_by_field_name("name") or _find_child(node, "identifier")
        if name_node is None:
            return
        qualified = f"{parent_name}.{_node_text(name_node)}"
        nodes.append(
            Node(
                name=qualified,
                type=NodeType.FUNCTION,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                metadata={
                    "macro": True,
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": structure_hash(node),
                },
            )
        )
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_function(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        class_name: str | None,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        context: _CExtractionContext,
    ) -> None:
        parsed_name = self._get_function_name(node)
        if parsed_name is None:
            return
        owner_name = class_name
        containing_name = parent_name
        if owner_name is None and parsed_name.qualifiers:
            resolved_scope = context.resolve_qualified_scope(parent_name, parsed_name.qualifiers)
            containing_name = resolved_scope.containing_name
            owner_name = resolved_scope.class_name
        qualified = f"{containing_name}.{parsed_name.name}"

        is_method = owner_name is not None
        meta = compute_metrics_and_hash(node, C_BRANCH_MAP)

        nodes.append(
            Node(
                name=qualified,
                type=NodeType.METHOD if is_method else NodeType.FUNCTION,
                file=file_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                metadata={
                    "metrics": meta.metrics.to_dict(),
                    "content_hash": content_hash(source, node.start_byte, node.end_byte),
                    "structure_hash": meta.structure_hash,
                },
            )
        )
        edges.append(Edge(source=containing_name, target=qualified, rel=RelType.CONTAINS))

        # Extract calls
        body = _find_child(node, "compound_statement")
        if body is not None:
            self._extract_calls(body, qualified, owner_name, edges, context)

    def _get_function_name(self, node: TSNode) -> _FunctionName | None:
        """Extract function name from various declarator patterns."""
        decl = _find_descendant(node, "function_declarator")
        if decl is None:
            # Try pointer_declarator -> function_declarator
            ptr = _find_child(node, "pointer_declarator")
            if ptr is not None:
                decl = _find_child(ptr, "function_declarator")
        if decl is None:
            return None
        target = decl.child_by_field_name("declarator") or decl
        qualified = _qualified_name_parts(target)
        if len(qualified) > 1:
            return _FunctionName(name=qualified[-1], qualifiers=tuple(qualified[:-1]))
        if qualified:
            return _FunctionName(name=qualified[0])
        name = _find_child(decl, "identifier") or _find_child(decl, "field_identifier")
        if name is not None:
            return _FunctionName(name=_node_text(name))
        destr = _find_child(decl, "destructor_name")
        if destr is not None:
            ident = _find_child(destr, "identifier")
            if ident is not None:
                return _FunctionName(name=f"~{_node_text(ident)}")
        return None

    def _extract_includes(
        self,
        root: TSNode,
        module_name: str,
        edges: list[Edge],
    ) -> None:
        for child in root.children:
            if child.type == "preproc_include":
                # Get the include path
                path_node = _find_child(child, "string_literal")
                if path_node is not None:
                    content = _find_child(path_node, "string_content")
                    if content is not None:
                        target = _node_text(content)
                        # Strip .h/.hpp extension, convert / to .
                        for ext in (".h", ".hpp", ".hxx"):
                            if target.endswith(ext):
                                target = target[: -len(ext)]
                        target = target.replace("/", ".")
                        edges.append(
                            Edge(
                                source=module_name,
                                target=target,
                                rel=RelType.IMPORTS,
                                metadata={"unresolved": True},
                            )
                        )

    def _extract_calls(
        self,
        root: TSNode,
        caller_name: str,
        class_name: str | None,
        edges: list[Edge],
        context: _CExtractionContext,
    ) -> None:
        stack: list[TSNode] = [root]
        while stack:
            node = stack.pop()
            if node.type == "call_expression":
                target = self._call_target(node, class_name, context)
                if target is not None:
                    name, resolved = target
                    edges.append(
                        Edge(
                            source=caller_name,
                            target=name,
                            rel=RelType.CALLS,
                            metadata={} if resolved else {"unresolved": True},
                        )
                    )
            for child in node.children:
                if child.type != "function_definition":
                    stack.append(child)

    def _call_target(
        self,
        call_node: TSNode,
        class_name: str | None,
        context: _CExtractionContext,
    ) -> tuple[str, bool] | None:
        func = call_node.children[0] if call_node.children else None
        if func is None:
            return None

        if func.type == "identifier":
            name = _node_text(func)
            if name in _BUILTINS:
                return None
            # Skip macro-like calls (ALL_CAPS identifiers)
            if name.isupper() and len(name) > 1 and name not in context.function_macros:
                return None
            # Skip compiler intrinsics and framework macros
            if name.startswith(_SKIP_PREFIXES):
                return None
            return (name, False)

        if func.type == "field_expression":
            # obj->method() / obj.method(): only emit if we know the class
            if class_name is not None:
                obj = _find_child(func, "field_identifier")
                if obj is not None:
                    return (_node_text(obj), False)
            # Unknown receiver — skip, these almost never resolve via suffix match
            return None

        if func.type == "template_function":
            parts = _qualified_name_parts(func)
            if parts:
                return self._qualified_call_target(parts, context)

        if func.type == "qualified_identifier":
            parts = _qualified_name_parts(func)
            if parts:
                return self._qualified_call_target(parts, context)

        return None

    def _qualified_call_target(
        self,
        parts: list[str],
        context: _CExtractionContext,
    ) -> tuple[str, bool] | None:
        if not parts:
            return None
        if parts[0] in _BUILTINS:
            return None
        name = parts[-1]
        if name in _BUILTINS or (name.isupper() and len(name) > 1 and name not in context.function_macros):
            return None
        return (".".join(parts), False)


def _find_child(node: TSNode, type_name: str) -> TSNode | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_descendant(node: TSNode, type_name: str) -> TSNode | None:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == type_name:
            return current
        stack.extend(reversed(current.children))
    return None


def _collect_context(root: TSNode, module_name: str, is_cpp: bool) -> _CExtractionContext:
    context = _CExtractionContext(
        is_cpp=is_cpp,
        namespaces=set(),
        classes=set(),
        types=set(),
        function_macros=set(),
    )
    _collect_symbols(root, module_name, context)
    return context


def _collect_symbols(root: TSNode, parent_name: str, context: _CExtractionContext) -> None:
    transparent = {
        "translation_unit",
        "declaration_list",
        "template_declaration",
        "preproc_ifdef",
        "preproc_if",
        "preproc_else",
        "preproc_elif",
        "linkage_specification",
    }
    for child in root.children:
        ctype = child.type
        if ctype in transparent:
            _collect_symbols(child, parent_name, context)
        elif ctype == "namespace_definition":
            name_node = _find_child(child, "namespace_identifier")
            if name_node is None:
                continue
            qualified = f"{parent_name}.{_node_text(name_node)}"
            context.namespaces.add(qualified)
            body = _find_child(child, "declaration_list")
            if body is not None:
                _collect_symbols(body, qualified, context)
        elif ctype in ("class_specifier", "struct_specifier", "union_specifier"):
            name_node = _find_child(child, "type_identifier")
            if name_node is not None:
                qualified = f"{parent_name}.{_node_text(name_node)}"
                if context.is_cpp or ctype in ("struct_specifier", "union_specifier"):
                    context.classes.add(qualified)
                else:
                    context.types.add(qualified)
                body = _find_child(child, "field_declaration_list")
                if body is not None:
                    _collect_symbols(body, qualified, context)
        elif ctype == "enum_specifier":
            name_node = _find_child(child, "type_identifier")
            if name_node is not None:
                context.types.add(f"{parent_name}.{_node_text(name_node)}")
        elif ctype == "type_definition":
            name_node = child.child_by_field_name("declarator") or _find_child(child, "type_identifier")
            type_node = (
                _find_child(child, "struct_specifier")
                or _find_child(child, "union_specifier")
                or _find_child(child, "enum_specifier")
            )
            if name_node is None or type_node is None:
                continue
            qualified = f"{parent_name}.{_node_text(name_node)}"
            if type_node.type in ("struct_specifier", "union_specifier"):
                context.classes.add(qualified)
            else:
                context.types.add(qualified)
        elif ctype == "declaration":
            _collect_symbols(child, parent_name, context)
        elif ctype == "preproc_function_def":
            name_node = child.child_by_field_name("name") or _find_child(child, "identifier")
            if name_node is not None:
                context.function_macros.add(_node_text(name_node))


def _qualified_name_parts(node: TSNode) -> list[str]:
    if node.type in ("identifier", "field_identifier", "type_identifier", "namespace_identifier"):
        return [_node_text(node)]
    if node.type == "destructor_name":
        ident = _find_child(node, "identifier")
        return [f"~{_node_text(ident)}"] if ident is not None else []
    if node.type == "template_function":
        name = node.child_by_field_name("name") or _find_child(node, "identifier")
        return _qualified_name_parts(name) if name is not None else []
    if node.type == "template_type":
        name = node.child_by_field_name("name") or _find_child(node, "type_identifier")
        return _qualified_name_parts(name) if name is not None else []
    if node.type == "qualified_identifier":
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
        parts: list[str] = []
        if scope is not None:
            parts.extend(_qualified_name_parts(scope))
        if name is not None:
            parts.extend(_qualified_name_parts(name))
        if parts:
            return parts
    return []


def _looks_like_cpp_header(source: bytes) -> bool:
    return any(marker in source for marker in _CPP_HEADER_MARKERS)


# --- Concrete extractors ---


class CExtractor(_CExtractorBase):
    extensions = [".c"]
    branch_map = C_BRANCH_MAP

    def __init__(self) -> None:
        import tree_sitter_c as tsc

        self._parser = Parser(Language(tsc.language()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        return self._extract(self._parser, source, file_path, module_name, is_cpp=False)


class CHeaderExtractor(_CExtractorBase):
    extensions = [".h"]
    branch_map = C_BRANCH_MAP

    def __init__(self) -> None:
        import tree_sitter_c as tsc

        self._parser = Parser(Language(tsc.language()))
        try:
            import tree_sitter_cpp as tscpp
        except ImportError:
            self._cpp_parser: Parser | None = None
        else:
            self._cpp_parser = Parser(Language(tscpp.language()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        if self._cpp_parser is not None and _looks_like_cpp_header(source):
            return self._extract(self._cpp_parser, source, file_path, module_name, is_cpp=True)
        return self._extract(self._parser, source, file_path, module_name, is_cpp=False)


class CppExtractor(_CExtractorBase):
    extensions = [".cpp", ".cc", ".cxx", ".cu", ".metal"]
    branch_map = C_BRANCH_MAP

    def __init__(self) -> None:
        import tree_sitter_cpp as tscpp

        self._parser = Parser(Language(tscpp.language()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        return self._extract(self._parser, source, file_path, module_name, is_cpp=True)


class CppHeaderExtractor(_CExtractorBase):
    extensions = [".hpp", ".hh", ".hxx", ".cuh"]
    branch_map = C_BRANCH_MAP

    def __init__(self) -> None:
        import tree_sitter_cpp as tscpp

        self._parser = Parser(Language(tscpp.language()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        return self._extract(self._parser, source, file_path, module_name, is_cpp=True)


# Register — each catches its own ImportError
try:
    register(CExtractor())
    register(CHeaderExtractor())
except ImportError:
    pass

try:
    register(CppExtractor())
    register(CppHeaderExtractor())
except ImportError:
    pass
