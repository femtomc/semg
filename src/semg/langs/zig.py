"""Zig language extractor."""
from __future__ import annotations

import tree_sitter_zig as tszig
from tree_sitter import Language, Node as TSNode, Parser

from semg.langs import ExtractResult, register
from semg.metrics import BranchMap, compute_metrics
from semg.model import Edge, Node, NodeType, RelType

_LANGUAGE = Language(tszig.language())
_PARSER = Parser(_LANGUAGE)

ZIG_BRANCH_MAP = BranchMap(
    branch_nodes=frozenset({
        "if_statement", "if_expression", "else_clause",
        "for_statement", "while_statement", "while_expression",
        "switch_expression", "switch_case",
        "catch_expression", "try_expression",
    }),
    boolean_operators=frozenset({"binary_expression"}),
    nesting_nodes=frozenset({
        "if_statement", "if_expression",
        "for_statement", "while_statement", "while_expression",
        "switch_expression",
    }),
    loop_nodes=frozenset({"for_statement", "while_statement", "while_expression"}),
    function_nodes=frozenset({"function_declaration"}),
    logical_operator_tokens=frozenset({"and", "or"}),
)

_BUILTINS = frozenset({
    "std", "print", "assert", "expect", "log",
})


class ZigExtractor:
    extensions = [".zig"]
    branch_map = ZIG_BRANCH_MAP

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult:
        tree = _PARSER.parse(source)
        nodes: list[Node] = []
        edges: list[Edge] = []
        self._walk_top_level(tree.root_node, module_name, file_path, nodes, edges)
        self._extract_imports(tree.root_node, module_name, edges)
        return ExtractResult(nodes=nodes, edges=edges)

    def _walk_top_level(
        self,
        root: TSNode,
        module_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        for child in root.children:
            if child.type == "variable_declaration":
                self._extract_variable_decl(child, module_name, file_path, nodes, edges)
            elif child.type == "function_declaration":
                self._extract_function(child, module_name, None, file_path, nodes, edges)
            elif child.type == "test_declaration":
                self._extract_test(child, module_name, file_path, nodes, edges)

    def _extract_variable_decl(
        self,
        node: TSNode,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        name_node = _find_child(node, "identifier")
        if name_node is None:
            return
        var_name = name_node.text.decode()

        # Check if it's a struct declaration
        struct_node = _find_child(node, "struct_declaration")
        if struct_node is not None:
            self._extract_struct(struct_node, var_name, parent_name, file_path, nodes, edges)
            return

        # Check if it's an enum or union
        enum_node = _find_child(node, "enum_declaration")
        if enum_node is not None:
            qualified = f"{parent_name}.{var_name}"
            nodes.append(Node(name=qualified, type=NodeType.TYPE, file=file_path, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))
            edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))
            return

        # UPPER_CASE = constant
        if var_name.isupper() or (var_name[0].isupper() and "_" in var_name and var_name == var_name.upper()):
            qualified = f"{parent_name}.{var_name}"
            nodes.append(Node(name=qualified, type=NodeType.CONSTANT, file=file_path, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))
            edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_struct(
        self,
        struct_node: TSNode,
        struct_name: str,
        parent_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        qualified = f"{parent_name}.{struct_name}"
        nodes.append(Node(
            name=qualified,
            type=NodeType.CLASS,  # Zig structs map to class in semg
            file=file_path,
            line=struct_node.start_point[0] + 1,
            end_line=struct_node.end_point[0] + 1,
        ))
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Walk struct body for methods and fields
        for child in struct_node.children:
            if child.type == "function_declaration":
                self._extract_function(child, qualified, qualified, file_path, nodes, edges)
            elif child.type == "variable_declaration":
                self._extract_variable_decl(child, qualified, file_path, nodes, edges)

    def _extract_function(
        self,
        node: TSNode,
        parent_name: str,
        struct_name: str | None,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        name_node = _find_child(node, "identifier")
        if name_node is None:
            return
        func_name = name_node.text.decode()
        qualified = f"{parent_name}.{func_name}"

        # Detect if this is a method (has self parameter)
        is_method = self._has_self_param(node)

        metrics = compute_metrics(node, self.branch_map)

        nodes.append(Node(
            name=qualified,
            type=NodeType.METHOD if is_method else NodeType.FUNCTION,
            file=file_path,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            metadata={"metrics": metrics.to_dict()},
        ))
        edges.append(Edge(source=parent_name, target=qualified, rel=RelType.CONTAINS))

        # Extract calls from function body
        body = _find_child(node, "block")
        if body is not None:
            self._extract_calls(body, qualified, struct_name if is_method else None, edges)

    def _extract_test(
        self,
        node: TSNode,
        module_name: str,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract test declarations as function nodes."""
        # Test name is a string literal
        string_node = _find_child(node, "string")
        if string_node is None:
            return
        content = _find_child(string_node, "string_content")
        if content is None:
            return
        test_name = content.text.decode().replace(" ", "_")
        qualified = f"{module_name}.test_{test_name}"

        nodes.append(Node(
            name=qualified,
            type=NodeType.FUNCTION,
            file=file_path,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            metadata={"test": True},
        ))
        edges.append(Edge(source=module_name, target=qualified, rel=RelType.CONTAINS))

    def _extract_imports(
        self,
        root: TSNode,
        module_name: str,
        edges: list[Edge],
    ) -> None:
        """Extract @import() calls as IMPORTS edges."""
        self._find_imports_recursive(root, module_name, edges)

    def _find_imports_recursive(self, node: TSNode, module_name: str, edges: list[Edge]) -> None:
        if node.type == "builtin_function":
            builtin_id = _find_child(node, "builtin_identifier")
            if builtin_id is not None and builtin_id.text == b"@import":
                args = _find_child(node, "arguments")
                if args is not None:
                    string_node = _find_child(args, "string")
                    if string_node is not None:
                        content = _find_child(string_node, "string_content")
                        if content is not None:
                            target = content.text.decode()
                            edges.append(Edge(
                                source=module_name,
                                target=target,
                                rel=RelType.IMPORTS,
                                metadata={"unresolved": True},
                            ))
                return
        for child in node.children:
            # Only scan top-level variable declarations for imports
            if node.type == "source_file" or node.type == "variable_declaration" or node.type == "field_expression":
                self._find_imports_recursive(child, module_name, edges)

    def _extract_calls(
        self,
        node: TSNode,
        caller_name: str,
        struct_name: str | None,
        edges: list[Edge],
    ) -> None:
        if node.type == "call_expression":
            target = self._call_target(node, struct_name)
            if target is not None:
                name, resolved = target
                edges.append(Edge(
                    source=caller_name,
                    target=name,
                    rel=RelType.CALLS,
                    metadata={} if resolved else {"unresolved": True},
                ))
        for child in node.children:
            if child.type == "function_declaration":
                continue
            self._extract_calls(child, caller_name, struct_name, edges)

    def _call_target(self, call_node: TSNode, struct_name: str | None) -> tuple[str, bool] | None:
        """Resolve call target from a call_expression node."""
        # call_expression's first child is the function reference
        func = call_node.children[0] if call_node.children else None
        if func is None:
            return None

        if func.type == "identifier":
            name = func.text.decode()
            if name in _BUILTINS:
                return None
            return (name, False)

        if func.type == "field_expression":
            parts = self._field_expression_parts(func)
            if not parts:
                return None

            # self.method() -> StructName.method
            if parts[0] == "self" and struct_name and len(parts) == 2:
                return (f"{struct_name}.{parts[1]}", True)

            # Skip std.* calls
            if parts[0] == "std":
                return None

            return (".".join(parts), False)

        if func.type == "builtin_function":
            return None  # @import, @intCast, etc.

        return None

    def _field_expression_parts(self, node: TSNode) -> list[str]:
        """Flatten a.b.c field_expression to ['a', 'b', 'c']."""
        if node.type == "identifier":
            return [node.text.decode()]
        if node.type == "field_expression":
            parts = []
            for child in node.children:
                if child.is_named:
                    parts.extend(self._field_expression_parts(child))
            return parts
        return []

    def _has_self_param(self, func_node: TSNode) -> bool:
        params = _find_child(func_node, "parameters")
        if params is None:
            return False
        for child in params.children:
            if child.type == "parameter":
                name = _find_child(child, "identifier")
                if name is not None and name.text == b"self":
                    return True
        return False


def _find_child(node: TSNode, type_name: str) -> TSNode | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


register(ZigExtractor())
