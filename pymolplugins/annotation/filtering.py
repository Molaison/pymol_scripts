"""Column expressions for CSV views, interpreted without executing Python."""

import ast
from itertools import chain
import keyword
import math
import operator
import re


COMPARISON_OPERATORS = ("==", "!=", "<", "<=", ">", ">=")
TEXT_OPERATORS = {"contains": operator.contains, "startswith": str.startswith, "endswith": str.endswith}
_PAREN_TOKEN = re.compile(r"""(?:"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?|\x60[^\x60]*\x60?)|([()])""")


def filter_parentheses(expression):
    """Return matched pairs and unmatched delimiters outside quoted values."""
    stack, extra, pairs = [], set(), {}
    for token in _PAREN_TOKEN.finditer(expression):
        if token.group(1) == "(":
            stack.append(token.start())
        elif token.group(1) == ")":
            if stack:
                pairs[stack.pop()] = token.start()
            else:
                extra.add(token.start())
    return pairs, extra.union(stack)


def remove_unmatched_parentheses(expression):
    _, extra = filter_parentheses(expression)
    return "".join(character for index, character in enumerate(expression) if index not in extra) if extra else expression


def column_expression(name):
    """Spell a column name for insertion into a filter."""
    if all(part.isidentifier() and not keyword.iskeyword(part) for part in name.split(".")):
        return name
    return f"`{name}`"


class FilterSyntaxError(ValueError):
    """A filter contains an unknown column or unsupported expression."""


def validate_filter_presets(raw, columns=None):
    """Validate portable named views; these never assign annotation labels."""
    if not isinstance(raw, list):
        raise ValueError("filter_presets must be a list")
    result, names = [], set()
    for preset in raw:
        if (not isinstance(preset, dict) or not {"name", "expression"} <= set(preset)
                or set(preset) - {"name", "expression", "sort_column", "sort_descending"}):
            raise ValueError("Each filter preset needs name/expression and optional sort_column/sort_descending")
        if any(not isinstance(preset[key], str) or not preset[key].strip() for key in ("name", "expression")):
            raise ValueError("Filter names and expressions must be nonempty strings")
        name = preset["name"].strip()
        expression = remove_unmatched_parentheses(preset["expression"]).strip()
        if not expression:
            raise ValueError("Filter expression is empty after removing unmatched parentheses")
        if name in names:
            raise ValueError("Duplicate filter name: " + name)
        names.add(name)
        sort = preset.get("sort_column", "")
        descending = preset.get("sort_descending", False)
        if not isinstance(sort, str) or type(descending) is not bool:
            raise ValueError(name + ": sort_column must be text and sort_descending must be boolean")
        if columns is not None:
            try:
                compile_filter(expression, columns)
                if sort and sort not in columns:
                    raise ValueError("Unknown sort column: " + sort)
            except ValueError as exc:
                raise ValueError("Filter %r: %s" % (name, exc)) from exc
        result.append({"name": name, "expression": expression,
                       "sort_column": sort, "sort_descending": descending})
    return result


def _quoted_columns(expression):
    result, aliases = [], {}
    index, quote = 0, ""
    while index < len(expression):
        character = expression[index]
        if quote:
            result.append(character)
            if character == "\\" and index + 1 < len(expression):
                index += 1
                result.append(expression[index])
            elif character == quote:
                quote = ""
        elif character in ("'", '"'):
            quote = character
            result.append(character)
        elif character == "`":
            end = expression.find("`", index + 1)
            if end < 0:
                raise FilterSyntaxError("Close the backtick around the column name")
            alias = f"__csv_column_{len(aliases)}"
            aliases[alias] = expression[index + 1:end]
            result.append(alias)
            index = end
        else:
            result.append(character)
        index += 1
    return "".join(result), aliases


def _column_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _column_name(node.value) + "." + node.attr
    raise FilterSyntaxError("Choose a CSV column or use a quoted literal")


def _empty(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):
        return float(value)


def _compare(left, right, operation):
    if isinstance(operation, (ast.Is, ast.IsNot)):
        empty = _empty(left)
        return not empty if isinstance(operation, ast.IsNot) else empty
    if _empty(left) or _empty(right):
        equal = _empty(left) and _empty(right)
        return equal if isinstance(operation, ast.Eq) else (
            not equal if isinstance(operation, ast.NotEq) else False
        )
    if isinstance(left, bool) or isinstance(right, bool):
        values = {"true": True, "false": False, "1": True, "0": False}
        left = values.get(str(left).strip().casefold())
        right = values.get(str(right).strip().casefold())
        if left is None or right is None:
            return False
    elif isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            left, right = _number(left), _number(right)
        except (TypeError, ValueError):
            return False
        if any(isinstance(value, float) and not math.isfinite(value) for value in (left, right)):
            return False
    operations = {
        ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
        ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    }
    return operations[type(operation)](left, right)


def _text_search(text):
    needle = str(text or "").strip().casefold()
    return lambda row, extra=(): not needle or any(
        needle in str(value).casefold() for value in chain(extra, row.values())
    )


def compile_view_filter(query, columns):
    """Compile a saved view's search or condition against an annotation entry."""
    text = str(query or "")
    predicate = compile_filter(text[6:], columns) if text.startswith("where:") else _text_search(text)
    return lambda entry: bool(predicate(entry.metadata, (entry.display_name, entry.source_path, entry.resolved_path)))


def compile_filter(expression, columns):
    """Compile a column condition into a callable accepting raw row metadata.

    Supports comparisons, and/or/not, parentheses, text matching functions, and
    ``is None``. Backticks quote names containing spaces or punctuation.
    The predicate accepts optional extra searchable text for ``text_search``.
    """
    text = str(expression).strip()
    if not text:
        return lambda row, extra=(): True
    text, aliases = _quoted_columns(text)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FilterSyntaxError(f"Invalid filter near column {exc.offset}: {exc.msg}") from exc
    known = set(columns)
    return _compile_node(tree.body, known, aliases)


def _compile_node(node, columns, aliases):
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
        return lambda row, extra=(): node.value
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = _column_name(node)
        if name in {"true", "false", "null"} and name not in columns:
            return lambda row, extra=(): {"true": True, "false": False, "null": None}[name]
        name = aliases.get(name, name)
        if name not in columns:
            raise FilterSyntaxError(f"Unknown CSV column {name!r}; choose a name from the column list")
        return lambda row, extra=(): row.get(name)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        if not isinstance(node.operand, ast.Constant) or not isinstance(node.operand.value, (int, float)):
            raise FilterSyntaxError("A sign must precede a number")
        value = node.operand.value * (-1 if isinstance(node.op, ast.USub) else 1)
        return lambda row, extra=(): value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _compile_node(node.operand, columns, aliases)
        return lambda row, extra=(): not value(row, extra)
    if isinstance(node, ast.BoolOp):
        values = [_compile_node(value, columns, aliases) for value in node.values]
        combine = all if isinstance(node.op, ast.And) else any
        return lambda row, extra=(): combine(value(row, extra) for value in values)
    if isinstance(node, ast.Compare):
        allowed = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot)
        if not all(isinstance(op, allowed) for op in node.ops):
            raise FilterSyntaxError("Use ==, !=, <, <=, >, >=, is None, or is not None")
        for op, value in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Is, ast.IsNot)) and not (isinstance(value, ast.Constant) and value.value is None):
                raise FilterSyntaxError("Use 'is None' or 'is not None' for empty cells")
        values = [_compile_node(value, columns, aliases) for value in [node.left] + node.comparators]
        return lambda row, extra=(): all(
            _compare(values[i](row, extra), values[i+1](row, extra), op) for i, op in enumerate(node.ops)
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "text_search":
        if len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                return _text_search(node.args[0].value)
        raise FilterSyntaxError("Use text_search with one quoted text value")
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "header_filter"
            and len(node.args) == 1 and not node.keywords):
        return _compile_node(node.args[0], columns, aliases)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in TEXT_OPERATORS and len(node.args) == 2 and not node.keywords):
        value, needle = [_compile_node(arg, columns, aliases) for arg in node.args]
        match = TEXT_OPERATORS[node.func.id]

        def text_match(row, extra=()):
            cell = value(row, extra)
            return match(str(cell if cell is not None else "").casefold(), str(needle(row, extra)).casefold())

        return text_match
    raise FilterSyntaxError("Unsupported filter; use column comparisons joined with and/or")
