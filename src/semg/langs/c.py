"""C and C++ extractor.

Handles .c, .h, .cpp, .hpp, .cc, .cxx, .hxx files.
C structs map to CLASS nodes, C++ classes map directly.
"""
from __future__ import annotations

from tree_sitter import Language, Node as TSNode, Parser

from semg.langs import ExtractResult, register
from semg.metrics import BranchMap, compute_metrics
from semg.model import Edge, Node, NodeType, RelType

_BUILTINS = frozenset({
    "printf", "fprintf", "sprintf", "snprintf", "scanf", "sscanf",
    "malloc", "calloc", "realloc", "free",
    "memcpy", "memset", "memmove", "memcmp",
    "strlen", "strcpy", "strncpy", "strcmp", "strncmp", "strcat",
    "sizeof", "assert", "exit", "abort",
    "fopen", "fclose", "fread", "fwrite", "fgets", "fputs",
    # C++ stdlib
    "std", "cout", "cerr", "endl",
    "make_shared", "make_unique", "move", "forward",
    "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast",
    "throw", "new", "delete",
})

C_BRANCH_MAP = BranchMap(
    branch_nodes=frozenset({
        "if_statement", "else_clause",
        "for_statement", "while_statement", "do_statement",
        "switch_statement", "case_statement",
        "conditional_expression",  # ternary
    }),
    boolean_operators=frozenset({"binary_expression"}),
    nesting_nodes=frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "switch_statement",
    }),
    loop_nodes=frozenset({"for_statement", "while_statement", "do_statement"}),
    function_nodes=frozenset({"function_definition"}),
    logical_operator_tokens=frozenset({"&&", "||"}),
)


class _CExtractorBase:
    """Shared extraction logic for C and C++."""

    def _extract(self, parser: Parser, source: bytes, file_path: str, module_name: str, is_cpp: bool) -> ExtractResult:
        tree = parser.parse(source)
        nodes: list[Node] = []
        edges: list[Edge] = []
        self._walk_top_level(tree.root_node, module_name, file_path, nodes, edges, is_cpp)
        self._extract_includes(tree.root_node, module_name, edges)
        return ExtractResult(nodes=nodes, edges=edges)

    def _walk_top_level(
        self,
        root: TSNode,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        is_cpp: bool,
    ) -> None:
        for child in root.children:
            # Descend into preprocessor conditionals transparently
            if child.type in ("preproc_ifdef", "preproc_if", "preproc_else", "preproc_elif"):
                self._walk_top_level(child, parent_name, file_path, nodes, edges, is_cpp)
                continue
            if child.type == "function_definition":
                self._extract_function(child, parent_name, None, file_path, nodes, edges)
            elif child.type == "type_definition":
                self._extract_typedef(child, parent_name, file_path, nodes, edges)
            elif child.type == "preproc_def":
                self._extract_define(child, parent_name, file_path, nodes, edges)
            elif child.type == "declaration":
                # Could be a global variable or function declaration
                pass
            elif is_cpp and child.type == "namespace_definition":
                self._extract_namespace(child, parent_name, file_path, nodes, edges)
            elif is_cpp and child.type in ("class_specifier", "struct_specifier"):
                self._extract_cpp_class(child, parent_name, file_path, nodes, edges)
            # Handle top-level class/struct that ends with semicolon (template specilization etc)
            if is_cpp and child.type == "declaration":
                for inner in child.children:
                    if inner.type in ("class_specifier", "struct_specifier"):
                        self._extract_cpp_class(inner, parent_name, file_path, nodes, edges)

    def _extract_namespace(
        self,
        node: TSNode,
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

        nodes.append(Node(name=qualified, type=NodeType.PACKAGE, file=file_path, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        body = _find_child(node, "declaration_list")
        if body is not None:
            self._walk_top_level(body, qualified, file_path, nodes, edges, is_cpp=True)

    def _extract_cpp_class(
        self,
        node: TSNode,
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

        nodes.append(Node(name=qualified, type=NodeType.CLASS, file=file_path, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Inheritance
        base_clause = _find_child(node, "base_class_clause")
        if base_clause is not None:
            for child in base_clause.children:
                if child.type == "type_identifier":
                    edges.append(Edge(
                        source=qualified, target=child.text.decode(),
                        rel=RelType.INHERITS, metadata={"unresolved": True},
                    ))

        # Methods in field_declaration_list
        body = _find_child(node, "field_declaration_list")
        if body is not None:
            for child in body.children:
                if child.type == "function_definition":
                    self._extract_function(child, qualified, qualified, file_path, nodes, edges)

    def _extract_typedef(
        self,
        node: TSNode,
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

        nodes.append(Node(name=qualified, type=NodeType.CLASS, file=file_path, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))
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
        nodes.append(Node(name=qualified, type=NodeType.CONSTANT, file=file_path, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_function(
        self,
        node: TSNode,
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
        metrics = compute_metrics(node, C_BRANCH_MAP)

        nodes.append(Node(
            name=qualified,
            type=NodeType.METHOD if is_method else NodeType.FUNCTION,
            file=file_path,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            metadata={"metrics": metrics.to_dict()},
        ))
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
                return f"~{_find_child(destr, 'identifier').text.decode()}" if _find_child(destr, "identifier") else None
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
                                target = target[:-len(ext)]
                        target = target.replace("/", ".")
                        edges.append(Edge(
                            source=module_name, target=target,
                            rel=RelType.IMPORTS, metadata={"unresolved": True},
                        ))

    def _extract_calls(
        self,
        node: TSNode,
        caller_name: str,
        class_name: str | None,
        edges: list[Edge],
    ) -> None:
        if node.type == "call_expression":
            target = self._call_target(node, class_name)
            if target is not None:
                name, resolved = target
                edges.append(Edge(
                    source=caller_name, target=name, rel=RelType.CALLS,
                    metadata={} if resolved else {"unresolved": True},
                ))
        for child in node.children:
            if child.type == "function_definition":
                continue
            self._extract_calls(child, caller_name, class_name, edges)

    def _call_target(self, call_node: TSNode, class_name: str | None) -> tuple[str, bool] | None:
        func = call_node.children[0] if call_node.children else None
        if func is None:
            return None

        if func.type == "identifier":
            name = func.text.decode()
            if name in _BUILTINS:
                return None
            return (name, False)

        if func.type == "field_expression":
            obj = _find_child(func, "field_identifier")
            # For C++: this->method() or obj.method() or obj->method()
            if obj is not None:
                return (obj.text.decode(), False)

        if func.type == "template_function":
            name = _find_child(func, "identifier")
            if name is not None:
                return (name.text.decode(), False)

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
    extensions = [".cpp", ".cc", ".cxx"]
    branch_map = C_BRANCH_MAP

    def __init__(self) -> None:
        import tree_sitter_cpp as tscpp
        self._parser = Parser(Language(tscpp.language()))

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        return self._extract(self._parser, source, file_path, module_name, is_cpp=True)


class CppHeaderExtractor(_CExtractorBase):
    extensions = [".hpp", ".hxx"]
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
