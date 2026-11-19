"""Keep table-header selections editable inside the saved Filter expression."""

import ast
from dataclasses import dataclass, field
from io import StringIO
import tokenize

from .filtering import _column_name, _quoted_columns, column_expression


@dataclass(frozen=True)
class HeaderChoice:
    kind: str
    value: object = None

    @property
    def key(self):
        return self.kind, self.value if self.kind == "category" else None

    def expression(self, column):
        if self.kind == "category":
            return f"{column} == {self.value!r}"
        if self.kind == "missing":
            return f"{column} is None"
        if self.kind == "other":
            return "(" + " and ".join([f"{column} is not None"] +
                                      [f"{column} != {label!r}" for label in self.value]) + ")"
        low, high = self.value
        return f"{low!r} <= {column} <= {high!r}"


@dataclass(frozen=True)
class HeaderFilters:
    base: str = ""
    selections: dict = field(default_factory=dict)

    def change(self, column, choice):
        selections = dict(self.selections)
        current = selections.get(column, ())
        remaining = tuple(item for item in current if item.key != choice.key)
        if choice.kind == "range" or len(remaining) == len(current):
            remaining += (choice,)
        if remaining:
            selections[column] = remaining
        else:
            selections.pop(column, None)
        return HeaderFilters(self.base, selections)

    def query(self, *, without=None):
        clauses = []
        for name, choices in self.selections.items():
            if name != without:
                column = column_expression(name)
                clauses.append("header_filter(" + " or ".join(choice.expression(column) for choice in choices) + ")")
        if not clauses:
            return self.base
        if self.base:
            source = self.base[6:] if self.base.startswith("where:") else f"text_search({self.base!r})"
            if source.strip():
                clauses.insert(0, f"({source})")
        return "where:" + " and ".join(clauses)


def _read_comparison(node, aliases):
    if not isinstance(node, ast.Compare):
        raise ValueError("Expected a header comparison")
    if len(node.ops) == 2 and all(isinstance(op, ast.LtE) for op in node.ops):
        column = _column_name(node.comparators[0])
        limits = (ast.literal_eval(node.left), ast.literal_eval(node.comparators[1]))
        if all(type(value) in (int, float) for value in limits):
            return aliases.get(column, column), HeaderChoice("range", limits)
    if len(node.ops) != 1:
        raise ValueError("Expected one header comparison")
    column = _column_name(node.left)
    operator, value = node.ops[0], ast.literal_eval(node.comparators[0])
    if isinstance(operator, ast.Eq) and isinstance(value, str):
        return aliases.get(column, column), HeaderChoice("category", value)
    if isinstance(operator, ast.Is) and value is None:
        return aliases.get(column, column), HeaderChoice("missing")
    raise ValueError("Unsupported header comparison")


def _read_choice(node, aliases):
    if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.And):
        return _read_comparison(node, aliases)
    missing, *excluded = node.values
    if (not isinstance(missing, ast.Compare) or len(missing.ops) != 1
            or not isinstance(missing.ops[0], ast.IsNot) or ast.literal_eval(missing.comparators[0]) is not None):
        raise ValueError("Expected the Other category")
    name = _column_name(missing.left)
    column = aliases.get(name, name)
    labels = []
    for value in excluded:
        if not isinstance(value, ast.Compare) or len(value.ops) != 1 or not isinstance(value.ops[0], ast.NotEq):
            raise ValueError("Expected an excluded category")
        name = _column_name(value.left)
        if aliases.get(name, name) != column:
            raise ValueError("Expected an excluded category from the same column")
        label = ast.literal_eval(value.comparators[0])
        if not isinstance(label, str):
            raise ValueError("Expected a text category")
        labels.append(label)
    return column, HeaderChoice("other", tuple(labels))


def _read_group(node, aliases):
    if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "header_filter"
            or len(node.args) != 1 or node.keywords):
        return None
    value = node.args[0]
    parts = value.values if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or) else [value]
    try:
        choices = [_read_choice(part, aliases) for part in parts]
    except ValueError:
        return None
    column = choices[0][0]
    return (column, tuple(choice for _, choice in choices)) if all(name == column for name, _ in choices) else None


def _manual_query(nodes, aliases):
    if not nodes:
        return ""
    if len(nodes) == 1:
        node = nodes[0]
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "text_search"
                and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)):
            text = node.args[0].value
            if isinstance(text, str) and not text.startswith("where:"):
                return text
    expression = ast.unparse(nodes[0] if len(nodes) == 1 else ast.BoolOp(op=ast.And(), values=nodes))
    if aliases:
        tokens = tokenize.generate_tokens(StringIO(expression).readline)
        expression = tokenize.untokenize(
            token._replace(string=column_expression(aliases[token.string]))
            if token.type == tokenize.NAME and token.string in aliases else token for token in tokens
        )
    return "where:" + expression


def read_header_filters(query):
    """Separate generated top-level header groups from the user's base Filter."""
    if not query.startswith("where:") or "header_filter" not in query:
        return HeaderFilters(query)
    expression, aliases = _quoted_columns(query[6:].strip())
    parsed = ast.parse(expression, mode="eval").body
    parts = parsed.values if isinstance(parsed, ast.BoolOp) and isinstance(parsed.op, ast.And) else [parsed]
    manual, selections = [], {}
    for part in parts:
        group = _read_group(part, aliases)
        if group is None or group[0] in selections:
            manual.append(part)
        else:
            selections[group[0]] = group[1]
    return HeaderFilters(_manual_query(manual, aliases), selections) if selections else HeaderFilters(query)
