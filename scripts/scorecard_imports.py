"""Project-internal import graph, dynamic imports and SCC analysis."""

from __future__ import annotations

import ast

from scorecard_config import ConfigError


def module_name(relative_path: str) -> str:
    if relative_path == "__init__.py":
        return ""
    if relative_path.endswith("/__init__.py"):
        return relative_path[: -len("/__init__.py")].replace("/", ".")
    return relative_path[:-3].replace("/", ".")


def _relative_to_root(path: str, root: str) -> str | None:
    if root == ".":
        return path
    prefix = root + "/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return None


def build_module_index(
    paths: list[str], source_roots: tuple[str, ...]
) -> tuple[dict[str, tuple[str, bool]], dict[str, str]]:
    """Map each production path to (module, is_package) and module -> path."""
    module_map: dict[str, str] = {}
    info: dict[str, tuple[str, bool]] = {}
    for path in paths:
        matches = [(root, _relative_to_root(path, root)) for root in source_roots]
        matches = [(root, relative) for root, relative in matches if relative is not None]
        if not matches:
            raise ConfigError("production file is outside every source root: %s" % path)
        root, relative = max(matches, key=lambda item: len(item[0]))
        module = module_name(relative)
        is_package = relative == "__init__.py" or relative.endswith("/__init__.py")
        info[path] = (module, is_package)
        if module:
            existing = module_map.get(module)
            if existing is not None and existing != path:
                raise ConfigError(
                    "two production files map to module %r: %s and %s"
                    % (module, existing, path)
                )
            module_map[module] = path
    return info, module_map


def _resolve_project_module(
    name: str, module_map: dict[str, str], module_prefixes: tuple[str, ...]
) -> str | None:
    candidates = [name]
    for prefix in module_prefixes:
        if name == prefix:
            candidates.append("")
        elif name.startswith(prefix + "."):
            candidates.append(name[len(prefix) + 1:])
    for candidate in candidates:
        if candidate in module_map:
            return module_map[candidate]
    return None


def is_type_checking_test(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _import_prefixes(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:size]) for size in range(len(parts), 0, -1)]


class ImportGraphVisitor(ast.NodeVisitor):
    def __init__(
        self,
        path: str,
        module: str,
        is_package: bool,
        module_map: dict[str, str],
        module_prefixes: tuple[str, ...],
    ) -> None:
        self.path = path
        self.module = module
        self.is_package = is_package
        self.module_map = module_map
        self.module_prefixes = module_prefixes
        self.depth = 0
        self.type_checking_depth = 0
        self.all_edges: set[tuple[str, str]] = set()
        self.non_type_checking_edges: set[tuple[str, str]] = set()
        self.top_level_edges: set[tuple[str, str]] = set()
        self.edge_details: list[dict] = []
        self.function_local_imports = 0
        self.dynamic_import_details: list[dict] = []

    @property
    def package(self) -> str:
        return self.module if self.is_package else self.module.rpartition(".")[0]

    @property
    def in_type_checking(self) -> bool:
        return self.type_checking_depth > 0

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    @staticmethod
    def _is_type_checking_test(node: ast.AST) -> bool:
        return is_type_checking_test(node)

    def visit_If(self, node: ast.If) -> None:
        is_type_checking = self._is_type_checking_test(node.test)
        self.visit(node.test)
        if is_type_checking:
            self.type_checking_depth += 1
            for statement in node.body:
                self.visit(statement)
            self.type_checking_depth -= 1
        else:
            for statement in node.body:
                self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def _record(self, base: str, aliases: list[str], lineno: int) -> None:
        targets: set[str] = set()
        if base:
            resolved = _resolve_project_module(base, self.module_map, self.module_prefixes)
            if resolved:
                targets.add(resolved)
        for alias in aliases:
            if alias == "*":
                continue
            child = base + "." + alias if base else alias
            resolved = _resolve_project_module(child, self.module_map, self.module_prefixes)
            if resolved:
                targets.add(resolved)
        for target in targets:
            edge = (self.path, target)
            self.all_edges.add(edge)
            self.edge_details.append(
                {
                    "from": self.path,
                    "to": target,
                    "type_checking": self.in_type_checking,
                    "source": "static",
                    "lineno": lineno,
                }
            )
            if not self.in_type_checking:
                self.non_type_checking_edges.add(edge)
                if self.depth == 0:
                    self.top_level_edges.add(edge)
        if targets and self.depth > 0:
            self.function_local_imports += 1

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        is_importlib = (
            isinstance(function, ast.Attribute)
            and function.attr == "import_module"
            and isinstance(function.value, ast.Name)
            and function.value.id == "importlib"
        )
        if is_importlib and node.args and isinstance(node.args[0], ast.Constant):
            name = node.args[0].value
            if isinstance(name, str):
                target = _resolve_project_module(name, self.module_map, self.module_prefixes)
                if target is not None:
                    edge = (self.path, target)
                    self.edge_details.append(
                        {
                            "from": self.path,
                            "to": target,
                            "type_checking": self.in_type_checking,
                            "source": "dynamic",
                            "lineno": node.lineno,
                        }
                    )
                    self.dynamic_import_details.append(
                        {
                            "from": self.path,
                            "to": target,
                            "module": name,
                            "lineno": node.lineno,
                            "type_checking": self.in_type_checking,
                            "function_local": self.depth > 0,
                        }
                    )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, [], node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            parts = self.package.split(".") if self.package else []
            trim = node.level - 1
            if trim:
                parts = parts[:-trim] if trim <= len(parts) else []
            base = ".".join(parts)
            if node.module:
                base = base + "." + node.module if base else node.module
        else:
            base = node.module or ""
        self._record(base, [alias.name for alias in node.names], node.lineno)
        self.generic_visit(node)


class RuntimeImportStatementVisitor(ast.NodeVisitor):
    """Collect one entry per runtime import statement.

    A from-import resolves its named aliases first (``from pkg import mod``
    becomes ``pkg/mod.py``) and only falls back to the base module when no
    alias resolves, so a package ``__init__`` is not counted as an extra edge
    for every from-import.  Function-local imports are runtime dependencies
    and are counted; imports under ``if TYPE_CHECKING`` are tagged separately.
    """

    def __init__(
        self,
        path: str,
        module: str,
        is_package: bool,
        module_map: dict[str, str],
        module_prefixes: tuple[str, ...],
    ) -> None:
        self.path = path
        self.module = module
        self.is_package = is_package
        self.module_map = module_map
        self.module_prefixes = module_prefixes
        self.type_checking_depth = 0
        self.statements: list[dict] = []

    @property
    def package(self) -> str:
        return self.module if self.is_package else self.module.rpartition(".")[0]

    @property
    def in_type_checking(self) -> bool:
        return self.type_checking_depth > 0

    def _resolve(self, name: str) -> str | None:
        return _resolve_project_module(name, self.module_map, self.module_prefixes)

    def _record(
        self, targets: list[str], lineno: int, source: str, imported: str = ""
    ) -> None:
        self.statements.append(
            {
                "path": self.path,
                "lineno": lineno,
                "source": source,
                "type_checking": self.in_type_checking,
                "targets": sorted(set(targets)),
                "imported": imported,
            }
        )

    def visit_If(self, node: ast.If) -> None:
        is_type_checking = is_type_checking_test(node.test)
        self.visit(node.test)
        if is_type_checking:
            self.type_checking_depth += 1
            for statement in node.body:
                self.visit(statement)
            self.type_checking_depth -= 1
        else:
            for statement in node.body:
                self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_Import(self, node: ast.Import) -> None:
        targets: list[str] = []
        for alias in node.names:
            for candidate in _import_prefixes(alias.name):
                resolved = self._resolve(candidate)
                if resolved:
                    targets.append(resolved)
                    break
        if targets:
            self._record(targets, node.lineno, "static")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            parts = self.package.split(".") if self.package else []
            trim = node.level - 1
            if trim:
                parts = parts[:-trim] if trim <= len(parts) else []
            base = ".".join(parts)
            if node.module:
                base = base + "." + node.module if base else node.module
        else:
            base = node.module or ""
        targets: list[str] = []
        for alias in node.names:
            if alias.name == "*":
                continue
            child = base + "." + alias.name if base else alias.name
            resolved = self._resolve(child)
            if resolved:
                targets.append(resolved)
        if targets:
            self._record(targets, node.lineno, "static")
        else:
            resolved = self._resolve(base)
            if resolved:
                self._record([resolved], node.lineno, "static")
        self.generic_visit(node)

    @staticmethod
    def _is_import_module_call(function: ast.AST) -> bool:
        if isinstance(function, ast.Name):
            return function.id == "import_module"
        return (
            isinstance(function, ast.Attribute)
            and function.attr == "import_module"
            and isinstance(function.value, ast.Name)
            and function.value.id == "importlib"
        )

    def visit_Call(self, node: ast.Call) -> None:
        if (
            self._is_import_module_call(node.func)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            name = node.args[0].value
            target = self._resolve(name)
            if target is not None:
                self._record([target], node.lineno, "dynamic", imported=name)
        self.generic_visit(node)


def collect_runtime_import_statements(
    trees: dict[str, ast.AST],
    module_info: dict[str, tuple[str, bool]],
    module_map: dict[str, str],
    module_prefixes: tuple[str, ...],
) -> list[dict]:
    statements: list[dict] = []
    for path, tree in trees.items():
        module, is_package = module_info[path]
        visitor = RuntimeImportStatementVisitor(
            path, module, is_package, module_map, module_prefixes
        )
        visitor.visit(tree)
        statements.extend(visitor.statements)
    return statements


def max_scc_size(nodes: set[str], edges: set[tuple[str, str]]) -> int:
    graph: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        if source in graph and target in graph:
            graph[source].append(target)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sizes: list[int] = []
    counter = 0
    for root in sorted(nodes):
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            vertex, position = work[-1]
            if position == 0:
                index[vertex] = low[vertex] = counter
                counter += 1
                stack.append(vertex)
                on_stack.add(vertex)
            descended = False
            neighbours = graph[vertex]
            for offset in range(position, len(neighbours)):
                target = neighbours[offset]
                if target not in index:
                    work[-1] = (vertex, offset + 1)
                    work.append((target, 0))
                    descended = True
                    break
                if target in on_stack:
                    low[vertex] = min(low[vertex], index[target])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[vertex])
            if low[vertex] == index[vertex]:
                size = 0
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    size += 1
                    if member == vertex:
                        break
                sizes.append(size)
    return max(sizes) if sizes else 0


def analyze_import_graph(
    trees: dict[str, ast.AST],
    module_info: dict[str, tuple[str, bool]],
    module_map: dict[str, str],
    module_prefixes: tuple[str, ...],
) -> dict:
    all_edges: set[tuple[str, str]] = set()
    non_type_checking_edges: set[tuple[str, str]] = set()
    top_level_edges: set[tuple[str, str]] = set()
    edge_details: list[dict] = []
    dynamic_details: list[dict] = []
    function_local_imports = 0
    for path, tree in trees.items():
        module, is_package = module_info[path]
        visitor = ImportGraphVisitor(path, module, is_package, module_map, module_prefixes)
        visitor.visit(tree)
        all_edges.update(visitor.all_edges)
        non_type_checking_edges.update(visitor.non_type_checking_edges)
        top_level_edges.update(visitor.top_level_edges)
        edge_details.extend(visitor.edge_details)
        dynamic_details.extend(visitor.dynamic_import_details)
        function_local_imports += visitor.function_local_imports
    nodes = set(trees)
    dynamic_edges = {(item["from"], item["to"]) for item in dynamic_details}
    dynamic_runtime_edges = {
        (item["from"], item["to"])
        for item in dynamic_details
        if not item["type_checking"]
    }
    static_edges = {
        (item["from"], item["to"])
        for item in edge_details
        if item["source"] == "static"
    }
    static_non_type_edges = {
        (item["from"], item["to"])
        for item in edge_details
        if item["source"] == "static" and not item["type_checking"]
    }
    static_top_level_edges = set(top_level_edges)
    return {
        "import_edges": len(static_edges),
        "import_edges_excluding_type_checking": len(static_non_type_edges),
        "top_level_import_edges": len(static_top_level_edges),
        "max_scc_size": max_scc_size(nodes, static_edges),
        "max_scc_size_excluding_type_checking_edges": max_scc_size(nodes, static_non_type_edges),
        "max_scc_size_top_level_imports": max_scc_size(nodes, static_top_level_edges),
        "function_local_imports": function_local_imports,
        "dynamic_import_edges": len({(item["from"], item["to"]) for item in dynamic_details}),
        "dynamic_import_edges_excluding_type_checking": len({(item["from"], item["to"]) for item in dynamic_details if not item["type_checking"]}),
        "dynamic_import_details": sorted(
            dynamic_details,
            key=lambda item: (item["from"], item["lineno"], item["to"]),
        ),
        "edge_details": sorted(
            edge_details,
            key=lambda item: (item["from"], item["lineno"], item["to"], item["source"]),
        ),
    }
