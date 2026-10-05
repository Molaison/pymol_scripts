"""Pure validation and value encoding for configurable annotation fields."""

import json
import math
import re


MAX_FIELDS = 16
FIELD_TYPES = ("single", "multiple", "text")


def field_key(name):
    value = re.sub(r"[^\w]+", "_", str(name).strip()).strip("_").lower()
    if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise ValueError("Annotation field names must contain 1–128 printable characters")
    return value


def validate_fields(raw):
    if not isinstance(raw, list) or len(raw) > MAX_FIELDS:
        raise ValueError(f"Annotation fields must be a list of at most {MAX_FIELDS} items")
    fields, names = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each annotation field must be an object")
        name = field_key(item.get("name", ""))
        if name in names:
            raise ValueError(f"Duplicate annotation field: {name}")
        kind = item.get("type", "single")
        required = item.get("required", False)
        if kind not in FIELD_TYPES or type(required) is not bool:
            raise ValueError(f"Invalid type or required flag for {name}")
        values = item.get("values", [])
        if not isinstance(values, list) or len(values) > 64 or any(not isinstance(value, str) or not value.strip() or len(value) > 256 for value in values):
            raise ValueError(f"Choices for {name} must be at most 64 nonempty strings")
        values = list(dict.fromkeys(value.strip() for value in values))
        if kind != "text" and not values:
            raise ValueError(f"Add at least one choice for {name}")
        fields.append({"name": name, "type": kind, "required": required, "values": values if kind != "text" else []})
        names.add(name)
    return fields


def default_fields(label, values):
    fields = [{"name": field_key(label), "type": "single", "required": True, "values": list(values)}]
    if fields[0]["name"] != "notes":
        fields.append({"name": "notes", "type": "text", "required": False, "values": []})
    return fields


def decode_value(field, value):
    if field["type"] != "multiple":
        return "" if value is None else str(value)
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        parsed = [str(value)]
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError(f"Stored value for {field['name']} is not a list of choices")
    return parsed


def encode_value(field, value):
    if field["type"] == "multiple":
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"Choose one or more values for {field['name']}")
        unknown = set(value) - set(field["values"])
        if unknown:
            raise ValueError(f"Unknown choices for {field['name']}: {', '.join(sorted(unknown))}")
        ordered = [item for item in field["values"] if item in value]
        return json.dumps(ordered, ensure_ascii=False) if ordered else None
    text = str(value or "")
    if field["type"] == "single" and text and text not in field["values"]:
        raise ValueError(f"Unknown choice for {field['name']}: {text}")
    return text if text.strip() else None


def missing_required(fields, annotations):
    missing = []
    for field in fields:
        value = decode_value(field, annotations.get(field["name"]))
        present = bool(value) if isinstance(value, list) else bool(value.strip())
        if field["required"] and not present:
            missing.append(field["name"])
    return missing


def validate_score_config(raw):
    if not isinstance(raw, dict):
        raise ValueError("Score guidance must be an object")
    columns = raw.get("columns")
    if columns is not None and (not isinstance(columns, list) or len(columns) > 6 or any(not isinstance(name, str) for name in columns)):
        raise ValueError("Choose at most six summary metrics")
    rules = raw.get("rules", {})
    if not isinstance(rules, dict) or len(rules) > 1000:
        raise ValueError("Score rules must be a bounded mapping")
    clean = {}
    for name, rule in rules.items():
        if not isinstance(name, str) or not isinstance(rule, dict):
            raise ValueError("Each score rule must name a CSV column")
        direction = rule.get("direction", "neutral")
        if direction not in {"neutral", "higher", "lower"}:
            raise ValueError(f"Choose a score direction for {name}")
        good, bad = rule.get("good"), rule.get("bad")
        for threshold in (good, bad):
            if threshold is not None and (type(threshold) not in (int, float) or not math.isfinite(threshold)):
                raise ValueError(f"Thresholds for {name} must be finite numbers")
        if direction == "neutral" and (good is not None or bad is not None):
            raise ValueError(f"Choose higher/lower before setting thresholds for {name}")
        if good is not None and bad is not None and ((direction == "higher" and good < bad) or (direction == "lower" and good > bad)):
            raise ValueError(f"Good and bad thresholds are reversed for {name}")
        pass_column = rule.get("pass_column", "")
        if not isinstance(pass_column, str):
            raise ValueError(f"Pass column for {name} must be a column name")
        if pass_column not in {"", "-"} and (good is not None or bad is not None):
            raise ValueError(f"Choose thresholds or a CSV pass column for {name}, not both")
        clean[name] = {"direction": direction, "good": good, "bad": bad, "pass_column": pass_column}
    return {"columns": list(dict.fromkeys(columns)) if columns is not None else None, "rules": clean}
