"""C and C++ extractor.

Handles .c, .h, .cpp, .hpp, .cc, .cxx, .hxx, .metal files.
C structs map to CLASS nodes, C++ classes map directly.
Metal Shading Language (.metal) is parsed as C++ since it shares the syntax.
"""

from __future__ import annotations

from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from smg.hashing import content_hash, structure_hash
from smg.langs import ExtractResult, register
from smg.metrics import BranchMap, compute_metrics_and_hash
from smg.model import Edge, Node, NodeType, RelType

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
        self._walk_top_level(tree.root_node, source, module_name, file_path, nodes, edges, is_cpp)
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
        is_cpp: bool,
    ) -> None:
        _TRANSPARENT = frozenset(
            {
                "preproc_ifdef",
                "preproc_if",
                "preproc_else",
                "preproc_elif",
                "linkage_specification",
                "declaration_list",
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
                    self._extract_function(child, source, pname, None, file_path, nodes, edges)
                elif ctype == "type_definition":
                    self._extract_typedef(child, source, pname, file_path, nodes, edges)
                elif ctype == "preproc_def":
                    self._extract_define(child, pname, file_path, nodes, edges)
                elif ctype == "declaration":
                    # Handle top-level class/struct in declarations (template specialization etc)
                    if is_cpp:
                        for inner in child.children:
                            if inner.type in ("class_specifier", "struct_specifier"):
                                self._extract_cpp_class(inner, source, pname, file_path, nodes, edges)
                elif is_cpp and ctype == "namespace_definition":
                    self._extract_namespace(child, source, pname, file_path, nodes, edges)
                elif is_cpp and ctype in ("class_specifier", "struct_specifier"):
                    self._extract_cpp_class(child, source, pname, file_path, nodes, edges)

    def _extract_namespace(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        name_node = _find_child(node, "namespace_identifier")
        if name_node is None:
            return
        ns_name = name_node.text.decode()
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
            self._walk_top_level(body, source, qualified, file_path, nodes, edges, is_cpp=True)

    def _extract_cpp_class(
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
        class_name = name_node.text.decode()
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
                if child.type == "type_identifier":
                    edges.append(
                        Edge(
                            source=qualified,
                            target=child.text.decode(),
                            rel=RelType.INHERITS,
                            metadata={"unresolved": True},
                        )
                    )

        # Methods in field_declaration_list
        body = _find_child(node, "field_declaration_list")
        if body is not None:
            for child in body.children:
                if child.type == "function_definition":
                    self._extract_function(child, source, qualified, qualified, file_path, nodes, edges)

    def _extract_typedef(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract typedef struct as a CLASS node."""
        struct_node = _find_child(node, "struct_specifier")
        if struct_node is None:
            return
        # The typedef name is the type_identifier at the end
        name_node = _find_child(node, "type_identifier")
        if name_node is None:
            return
        struct_name = name_node.text.decode()
        qualified = f"{parent_name}.{struct_name}"

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
        name = name_node.text.decode()
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

    def _extract_function(
        self,
        node: TSNode,
        source: bytes,
        parent_name: str,
        class_name: str | None,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        func_name = self._get_function_name(node)
        if func_name is None:
            return
        qualified = f"{parent_name}.{func_name}"

        is_method = class_name is not None
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
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Extract calls
        body = _find_child(node, "compound_statement")
        if body is not None:
            self._extract_calls(body, qualified, class_name, edges)

    def _get_function_name(self, node: TSNode) -> str | None:
        """Extract function name from various declarator patterns."""
        decl = _find_child(node, "function_declarator")
        if decl is None:
            # Try pointer_declarator -> function_declarator
            ptr = _find_child(node, "pointer_declarator")
            if ptr is not None:
                decl = _find_child(ptr, "function_declarator")
        if decl is None:
            return None
        # Name can be identifier or field_identifier (for methods)
        name = _find_child(decl, "identifier") or _find_child(decl, "field_identifier")
        if name is None:
            # Could be a destructor or special function
            destr = _find_child(decl, "destructor_name")
            if destr is not None:
                return (
                    f"~{_find_child(destr, 'identifier').text.decode()}" if _find_child(destr, "identifier") else None
                )
            return None
        return name.text.decode()

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
                        target = content.text.decode()
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
    ) -> None:
        stack: list[TSNode] = [root]
        while stack:
            node = stack.pop()
            if node.type == "call_expression":
                target = self._call_target(node, class_name)
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

    def _call_target(self, call_node: TSNode, class_name: str | None) -> tuple[str, bool] | None:
        func = call_node.children[0] if call_node.children else None
        if func is None:
            return None

        if func.type == "identifier":
            name = func.text.decode()
            if name in _BUILTINS:
                return None
            # Skip macro-like calls (ALL_CAPS identifiers)
            if name.isupper() and len(name) > 1:
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
                    return (obj.text.decode(), False)
            # Unknown receiver — skip, these almost never resolve via suffix match
            return None

        if func.type == "template_function":
            name = _find_child(func, "identifier")
            if name is not None:
                n = name.text.decode()
                if n in _BUILTINS or (n.isupper() and len(n) > 1):
                    return None
                return (n, False)

        return None


def _find_child(node: TSNode, type_name: str) -> TSNode | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


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
        # Use C parser for .h files by default
        import tree_sitter_c as tsc

        self._parser = Parser(Language(tsc.language()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
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
    extensions = [".hpp", ".hxx", ".cuh"]
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
