"""AST metrics for the maintainability scorecard.

Every counter is deliberately source-only and approximate.  The definitions in
SKILL.md describe the exact rules; this module is the executable form of them.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize

from scorecard_config import CustomCounter, SharedStateConfig, WriteInterfacesConfig


def is_single_private(name: str) -> bool:
    return len(name) > 1 and name.startswith("_") and not name.startswith("__")


def code_line_count(text: str) -> tuple[int, bool]:
    """Count non-blank non-comment physical lines; docstring bodies count as code."""
    lines = text.splitlines()
    comment_lines: set[int] = set()
    tokenized = True
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT and not token.line[: token.start[1]].strip():
                comment_lines.add(token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        tokenized = False
    count = 0
    for number, line in enumerate(lines, 1):
        if not line.strip() or number in comment_lines:
            continue
        if not tokenized and line.lstrip().startswith("#"):
            continue
        count += 1
    return count, tokenized


def _span(node: ast.AST) -> tuple[int, int]:
    return (int(node.lineno), int(node.end_lineno))


def _nested_ranges(node: ast.AST) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ranges.append(_span(child))
    return ranges


def _merged_length(ranges: list[tuple[int, int]]) -> int:
    if not ranges:
        return 0
    ordered = sorted(ranges)
    total = 0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end + 1:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start + 1
            cur_start, cur_end = start, end
    total += cur_end - cur_start + 1
    return total


class ComplexityVisitor(ast.NodeVisitor):
    """Count decisions in a function's own AST span, not nested definitions."""

    def __init__(self) -> None:
        self.value = 1

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(
            node,
            (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While,
             ast.ExceptHandler, ast.match_case, ast.Assert),
        ):
            self.value += 1
        elif isinstance(node, ast.BoolOp):
            self.value += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            self.value += 1 + len(node.ifs)
        super().generic_visit(node)

    def _nested_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        # Decorators precede the nested definition's AST span, so they belong
        # to the enclosing function's own lines, unlike its body or signature.
        for decorator in node.decorator_list:
            self.visit(decorator)

    visit_FunctionDef = _nested_definition
    visit_AsyncFunctionDef = _nested_definition
    visit_ClassDef = _nested_definition


def function_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    visitor = ComplexityVisitor()
    # Do not visit the function's own decorators: they precede its AST span.
    for field, value in ast.iter_fields(node):
        if field == "decorator_list":
            continue
        if isinstance(value, ast.AST):
            visitor.visit(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    visitor.visit(item)
    return visitor.value


def _function_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int]:
    args = node.args
    positional_only = [item for item in getattr(args, "posonlyargs", []) if item.arg not in {"self", "cls"}]
    positional_or_keyword = [item for item in args.args if item.arg not in {"self", "cls"}]
    keyword_only = [item for item in args.kwonlyargs if item.arg not in {"self", "cls"}]
    return {
        "positional_only": len(positional_only),
        "positional_or_keyword": len(positional_or_keyword),
        "keyword_only": len(keyword_only),
        "varargs": int(args.vararg is not None),
        "kwargs": int(args.kwarg is not None),
        "count": len(positional_only) + len(positional_or_keyword) + len(keyword_only),
    }


class FunctionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.functions: list[dict] = []
        self.current_path = ""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        start, end = _span(node)
        span = end - start + 1
        own = span - _merged_length(_nested_ranges(node))
        self.functions.append(
            {
                "path": self.current_path,
                "qualname": ".".join(self.stack + [node.name]),
                "lineno": start,
                "span_lines": span,
                "own_lines": own,
                "complexity": function_complexity(node),
                "parameters": _function_parameters(node),
            }
        )
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def collect_functions(trees: dict[str, ast.AST]) -> list[dict]:
    visitor = FunctionVisitor()
    for path, tree in trees.items():
        visitor.current_path = path
        visitor.visit(tree)
    return visitor.functions


def _word_pattern(word: str) -> str:
    """Build one word-boundary pattern.

    General words treat underscores and hyphens as separators, so
    ``legacy_path`` counts and ``legacy2`` does not.  ``compat`` is narrower:
    only a standalone token or a ``compat_``/``compat-`` prefix counts, so
    ``compatible``/``compatibility`` and ``my_compat`` do not.
    """
    escaped = re.escape(word)
    if word.casefold() == "compat":
        return r"(?<![A-Za-z0-9_])%s(?:_|-|(?![A-Za-z0-9_]))" % escaped
    return r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % escaped


class WordCounter:
    """Count configured residue words in identifiers and string literals."""

    def __init__(self, words: tuple[str, ...]) -> None:
        self.words = tuple(words)
        groups: list[str] = []
        self._group_words: dict[str, str] = {}
        for index, word in enumerate(self.words):
            group = "w%d" % index
            groups.append("(?P<%s>%s)" % (group, _word_pattern(word)))
            self._group_words[group] = word
        self._regex = re.compile("|".join(groups), re.IGNORECASE)

    def count(self, text: str) -> dict[str, int]:
        counts = {word: 0 for word in self.words}
        for match in self._regex.finditer(text):
            counts[self._group_words[match.lastgroup]] += 1
        return counts


def _iter_word_texts(node: ast.AST):
    """Yield the identifier/string payloads the residue counter inspects."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Attribute):
        yield node.attr
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, ast.arg):
        yield node.arg
    elif isinstance(node, ast.keyword):
        yield node.arg or ""
    elif isinstance(node, ast.alias):
        yield node.name or ""
        yield node.asname or ""
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value


def fallback_metrics(
    tree: ast.AST,
    word_counter: WordCounter,
    contract_regex: re.Pattern[str],
) -> dict:
    word_counts = {word: 0 for word in word_counter.words}
    except_exception = 0
    bare_except = 0
    contract_strings: set[str] = set()
    contract_occurrences = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                bare_except += 1
            else:
                names: list[str] = []
                if isinstance(node.type, ast.Name):
                    names = [node.type.id]
                elif isinstance(node.type, ast.Tuple):
                    names = [
                        item.id for item in node.type.elts if isinstance(item, ast.Name)
                    ]
                if "Exception" in names:
                    except_exception += 1
        for text in _iter_word_texts(node):
            for word, amount in word_counter.count(text).items():
                word_counts[word] += amount
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and contract_regex.fullmatch(node.value)
        ):
            contract_strings.add(node.value)
            contract_occurrences += 1
    return {
        "word_counts": word_counts,
        "legacy_words": sum(word_counts.values()),
        "contract_strings": contract_strings,
        "versioned_contract_strings_distinct": len(contract_strings),
        "versioned_contract_strings_occurrences": contract_occurrences,
        "except_exception": except_exception,
        "bare_except": bare_except,
        "broad_except_total": except_exception + bare_except,
    }


def _private_defined_names(tree: ast.AST) -> set[str]:
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if is_single_private(node.name):
                defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if is_single_private(node.id):
                defined.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            if is_single_private(node.attr):
                defined.add(node.attr)
    return defined


def private_access_count(tree: ast.AST) -> int:
    """Private attribute reads whose name is not defined anywhere in this file.

    Any single-underscore private name defined in the accessing file -- a
    def/class name, a Name assignment target, or an Attribute assignment target
    such as ``self._x =`` -- makes every same-named attribute access in that
    file local for this metric.
    """
    defined = _private_defined_names(tree)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and is_single_private(node.attr)
        and node.attr not in defined
    )


class SharedStateVisitor(ast.NodeVisitor):
    """Approximate shared-state traffic through configured receiver objects."""

    def __init__(self, config: SharedStateConfig) -> None:
        self.receiver_names = frozenset(config.receiver_names)
        self.receiver_attributes = frozenset(config.receiver_attributes)
        self.gets = 0
        self.subscript_reads = 0
        self.writes = 0
        self.pops = 0
        self.setdefaults = 0
        self.written_keys: set[str] = set()

    def _is_receiver(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.receiver_names
        if isinstance(node, ast.Attribute):
            return node.attr in self.receiver_attributes
        return False

    @staticmethod
    def _constant_key(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _update_keys(self, node: ast.Call) -> None:
        for argument in node.args:
            if isinstance(argument, ast.Dict):
                for key in argument.keys:
                    value = self._constant_key(key) if key is not None else None
                    if value is not None:
                        self.written_keys.add(value)
        for keyword in node.keywords:
            if keyword.arg:
                self.written_keys.add(keyword.arg)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Attribute) and self._is_receiver(function.value):
            if function.attr == "get":
                self.gets += 1
            elif function.attr == "update":
                self.writes += 1
                self._update_keys(node)
            elif function.attr == "pop":
                self.pops += 1
            elif function.attr == "setdefault":
                self.setdefaults += 1
                if node.args:
                    key = self._constant_key(node.args[0])
                    if key is not None:
                        self.written_keys.add(key)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._is_receiver(node.value):
            if isinstance(node.ctx, ast.Load):
                self.subscript_reads += 1
            else:
                self.writes += 1
                key = self._constant_key(node.slice)
                if key is not None:
                    self.written_keys.add(key)
        self.generic_visit(node)

    def _attribute_write(self, target: ast.AST) -> None:
        if isinstance(target, ast.Attribute) and self._is_receiver(target.value):
            self.writes += 1
            self.written_keys.add(target.attr)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._attribute_write(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._attribute_write(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._attribute_write(node.target)
        self.generic_visit(node)

    @property
    def accesses(self) -> int:
        return self.gets + self.subscript_reads + self.writes + self.pops + self.setdefaults


def shared_state_metrics(trees: dict[str, ast.AST], config: SharedStateConfig) -> dict:
    totals = {
        "gets": 0,
        "subscript_reads": 0,
        "writes": 0,
        "pops": 0,
        "setdefaults": 0,
    }
    keys_by_file: dict[str, set[str]] = {}
    for path, tree in trees.items():
        visitor = SharedStateVisitor(config)
        visitor.visit(tree)
        for key in totals:
            totals[key] += getattr(visitor, key)
        if visitor.written_keys:
            keys_by_file[path] = set(visitor.written_keys)
    key_files: dict[str, set[str]] = {}
    for path, keys in keys_by_file.items():
        for key in keys:
            key_files.setdefault(key, set()).add(path)
    shared_keys = sum(
        1 for files in key_files.values() if len(files) >= config.file_threshold
    )
    return {
        "receiver_names": list(config.receiver_names),
        "receiver_attributes": list(config.receiver_attributes),
        "key_file_threshold": config.file_threshold,
        "accesses": sum(totals.values()),
        **totals,
        "keys_written_by_ge_n_files": shared_keys,
    }


def write_interface_sites(
    trees: dict[str, ast.AST], config: WriteInterfacesConfig
) -> dict:
    call_names = frozenset(config.call_names)
    adapters = frozenset(config.adapter_files)
    sites: list[dict] = []
    for path, tree in trees.items():
        if path in adapters:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            callee = None
            if isinstance(function, ast.Attribute) and function.attr in call_names:
                callee = function.attr
            elif isinstance(function, ast.Name) and function.id in call_names:
                callee = function.id
            if callee is not None:
                sites.append({"path": path, "lineno": node.lineno, "callee": callee})
    sites.sort(key=lambda site: (site["path"], site["lineno"], site["callee"]))
    return {
        "call_names": list(config.call_names),
        "adapter_files": list(config.adapter_files),
        "count": len(sites),
        "files": len({site["path"] for site in sites}),
        "sites": sites,
    }


def custom_counter_metrics(
    production: dict[str, str],
    trees: dict[str, ast.AST],
    counters: tuple[CustomCounter, ...],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for counter in counters:
        if counter.regex is not None:
            pattern = re.compile(counter.regex)
            result[counter.name] = sum(
                len(pattern.findall(text)) for text in production.values()
            )
        else:
            names = frozenset(counter.call_names)
            total = 0
            for tree in trees.values():
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    function = node.func
                    if isinstance(function, ast.Attribute) and function.attr in names:
                        total += 1
                    elif isinstance(function, ast.Name) and function.id in names:
                        total += 1
            result[counter.name] = total
    return result


def test_metrics(texts: dict[str, str], mock_helpers: tuple[str, ...]) -> dict:
    private_attrs = 0
    mock_count = 0
    parse_errors: list[dict] = []
    helpers = frozenset(mock_helpers)
    for path, text in texts.items():
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError) as exc:
            parse_errors.append({"path": path, "error": str(exc)})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and is_single_private(node.attr):
                private_attrs += 1
            elif isinstance(node, ast.Name) and node.id in helpers:
                mock_count += 1
            elif isinstance(node, ast.Attribute) and node.attr in helpers:
                mock_count += 1
    return {
        "test_files": len(texts),
        "test_lines": sum(len(text.splitlines()) for text in texts.values()),
        "private_attr_accesses": private_attrs,
        "mock_helpers": mock_count,
        "parse_errors": parse_errors,
    }
