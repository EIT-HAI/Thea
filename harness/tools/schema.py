"""Validation for the JSON Schema subset used by tool definitions."""

from __future__ import annotations

import math
from typing import Any

JSON_TYPES = {
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
}

SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$comment",
        "$id",
        "$schema",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "default",
        "deprecated",
        "description",
        "enum",
        "examples",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "multipleOf",
        "oneOf",
        "properties",
        "readOnly",
        "required",
        "title",
        "type",
        "writeOnly",
    }
)


def validate_schema_definition(schema: Any) -> str:
    """Return an error for malformed declarations in the supported subset."""
    return _validate_schema_definition(schema, path="inputSchema", root=True)


def validate_arguments(schema: Any, arguments: Any) -> str:
    """Return an explanation when arguments violate the supported schema subset.

    An empty string means that the arguments are valid. Tool input schemas are
    object schemas, matching the ``inputSchema`` field in a tool definition.
    """
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    if not isinstance(schema, dict) or not schema:
        return ""
    return _validate_schema_value(arguments, schema, path="")


def _validate_object_arguments(schema: Any, arguments: Any, *, path: str) -> str:
    if not isinstance(arguments, dict):
        return f"{path or 'arguments'} must be an object"
    if not isinstance(schema, dict) or not schema:
        return ""

    properties = _mapping_or_empty(schema.get("properties"))
    return (
        _validate_required_properties(schema, arguments, path=path)
        or _validate_additional_properties(
            schema,
            arguments,
            properties,
            path=path,
        )
        or _validate_property_values(arguments, properties, path=path)
    )


def _validate_schema_value(value: Any, schema: dict[str, Any], *, path: str) -> str:
    validators = (
        _validate_json_type,
        _validate_const,
        _validate_enum,
        _validate_compositions,
        _validate_number,
        _validate_string,
        _validate_array,
        _validate_nested_object,
    )
    for validator in validators:
        error = validator(value, schema, path=path)
        if error:
            return error
    return ""


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _validate_required_properties(
    schema: dict[str, Any],
    arguments: dict[str, Any],
    *,
    path: str,
) -> str:
    required = schema.get("required")
    required = required if isinstance(required, list) else []
    missing = next((key for key in required if key not in arguments), None)
    if missing is None:
        return ""
    if path:
        return f"missing required property {path}.{missing}"
    return f"missing required property {missing!r}"


def _validate_additional_properties(
    schema: dict[str, Any],
    arguments: dict[str, Any],
    properties: dict[str, Any],
    *,
    path: str,
) -> str:
    additional = schema.get("additionalProperties")
    unexpected = sorted(set(arguments) - set(properties))
    if not unexpected or additional is True or additional is None:
        return ""
    if additional is False:
        prefix = f"{path}: " if path else ""
        return prefix + "unexpected properties: " + ", ".join(unexpected)
    if not isinstance(additional, dict):
        return ""
    for key in unexpected:
        property_path = f"{path}.{key}" if path else key
        error = _validate_schema_value(
            arguments[key],
            additional,
            path=property_path,
        )
        if error:
            return error
    return ""


def _validate_property_values(
    arguments: dict[str, Any],
    properties: dict[str, Any],
    *,
    path: str,
) -> str:
    for key, value in arguments.items():
        property_schema = properties.get(key)
        if not isinstance(property_schema, dict):
            continue
        property_path = f"{path}.{key}" if path else str(key)
        error = _validate_schema_value(value, property_schema, path=property_path)
        if error:
            return error
    return ""


def _expected_types(schema: dict[str, Any]) -> list[str]:
    expected = schema.get("type")
    if isinstance(expected, list):
        return [item for item in expected if isinstance(item, str)]
    return [expected] if isinstance(expected, str) else []


def _validate_json_type(value: Any, schema: dict[str, Any], *, path: str) -> str:
    expected_types = _expected_types(schema)
    if not expected_types or any(
        _matches_json_type(value, item) for item in expected_types
    ):
        return ""
    return f"{path} must be " + " or ".join(expected_types)


def _validate_enum(value: Any, schema: dict[str, Any], *, path: str) -> str:
    choices = schema.get("enum")
    if not isinstance(choices, list) or value in choices:
        return ""
    return f"{path} must be one of {choices!r}"


def _validate_const(value: Any, schema: dict[str, Any], *, path: str) -> str:
    if "const" not in schema or value == schema.get("const"):
        return ""
    return f"{path} must equal {schema.get('const')!r}"


def _validate_compositions(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        errors = [
            _validate_schema_value(value, candidate, path=path)
            for candidate in any_of
            if isinstance(candidate, dict)
        ]
        if errors and all(errors):
            return f"{path or 'arguments'} does not match any allowed schema"

    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        matches = sum(
            not _validate_schema_value(value, candidate, path=path)
            for candidate in one_of
            if isinstance(candidate, dict)
        )
        if matches != 1:
            return f"{path or 'arguments'} must match exactly one allowed schema"

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for candidate in all_of:
            if not isinstance(candidate, dict):
                continue
            error = _validate_schema_value(value, candidate, path=path)
            if error:
                return error
    return ""


def _validate_string(value: Any, schema: dict[str, Any], *, path: str) -> str:
    if not isinstance(value, str):
        return ""
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if isinstance(minimum, int) and len(value) < minimum:
        return f"{path} must contain at least {minimum} characters"
    if isinstance(maximum, int) and len(value) > maximum:
        return f"{path} must contain at most {maximum} characters"
    return ""


def _validate_number(value: Any, schema: dict[str, Any], *, path: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    if not math.isfinite(float(value)):
        return f"{path} must be a finite number"

    checks = (
        ("minimum", lambda bound: value >= bound, "greater than or equal to"),
        ("maximum", lambda bound: value <= bound, "less than or equal to"),
        ("exclusiveMinimum", lambda bound: value > bound, "greater than"),
        ("exclusiveMaximum", lambda bound: value < bound, "less than"),
    )
    for keyword, accepts, phrase in checks:
        if keyword not in schema:
            continue
        bound = schema[keyword]
        if not accepts(bound):
            return f"{path} must be {phrase} {bound}"

    if "multipleOf" in schema:
        multiple = schema["multipleOf"]
        quotient = float(value) / float(multiple)
        if not math.isclose(
            quotient,
            round(quotient),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            return f"{path} must be a multiple of {multiple}"
    return ""


def _validate_array(value: Any, schema: dict[str, Any], *, path: str) -> str:
    if not isinstance(value, list):
        return ""
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and len(value) < minimum:
        return f"{path} must contain at least {minimum} items"
    if isinstance(maximum, int) and len(value) > maximum:
        return f"{path} must contain at most {maximum} items"
    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        return ""
    for index, item in enumerate(value):
        error = _validate_schema_value(
            item,
            item_schema,
            path=f"{path}[{index}]",
        )
        if error:
            return error
    return ""


def _validate_nested_object(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    if not isinstance(value, dict):
        return ""
    return _validate_object_arguments(schema, value, path=path)


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def _validate_schema_definition(
    schema: Any,
    *,
    path: str,
    root: bool = False,
) -> str:
    if not isinstance(schema, dict):
        return f"{path} must be a mapping"

    validators = (
        lambda: _validate_supported_keywords(schema, path=path),
        lambda: _validate_schema_types(schema, path=path, root=root),
        lambda: _validate_enum_declaration(schema, path=path),
        lambda: _validate_size_declarations(schema, path=path),
        lambda: _validate_numeric_declarations(schema, path=path),
        lambda: _validate_schema_properties(schema, path=path),
        lambda: _validate_required_declaration(schema, path=path),
        lambda: _validate_schema_items(schema, path=path),
        lambda: _validate_schema_compositions(schema, path=path),
        lambda: _validate_additional_properties_schema(schema, path=path),
    )
    for validate in validators:
        error = validate()
        if error:
            return error
    return ""


def _validate_supported_keywords(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    unsupported = sorted(set(schema) - SUPPORTED_SCHEMA_KEYWORDS)
    if not unsupported:
        return ""
    return f"{path} contains unsupported keywords: {', '.join(unsupported)}"


def _validate_enum_declaration(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    if "enum" not in schema:
        return ""
    choices = schema["enum"]
    if not isinstance(choices, list) or not choices:
        return f"{path}.enum must be a non-empty list"
    return ""


def _validate_size_declarations(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
        if keyword not in schema:
            continue
        value = schema[keyword]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"{path}.{keyword} must be a non-negative integer"
    return ""


def _validate_numeric_declarations(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    for keyword in (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    ):
        if keyword not in schema:
            continue
        value = schema[keyword]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            return f"{path}.{keyword} must be a finite number"

    if "multipleOf" in schema:
        value = schema["multipleOf"]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        ):
            return f"{path}.multipleOf must be a positive finite number"

    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if (
        isinstance(minimum, (int, float))
        and not isinstance(minimum, bool)
        and isinstance(maximum, (int, float))
        and not isinstance(maximum, bool)
        and minimum > maximum
    ):
        return f"{path}.minimum must not exceed {path}.maximum"
    return ""


def _validate_schema_types(
    schema: dict[str, Any],
    *,
    path: str,
    root: bool,
) -> str:
    if "type" in schema:
        declared = schema["type"]
        if not isinstance(declared, str) and (
            not isinstance(declared, list)
            or not declared
            or not all(isinstance(item, str) for item in declared)
        ):
            return f"{path}.type must be a string or non-empty list of strings"
    expected = _expected_types(schema)
    unknown = sorted(set(expected) - JSON_TYPES)
    if unknown:
        return f"{path}.type contains unsupported values: {', '.join(unknown)}"
    if root and expected and "object" not in expected:
        return f"{path}.type must include 'object'"
    return ""


def _validate_schema_properties(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    if "properties" not in schema:
        return ""
    properties = schema["properties"]
    if not isinstance(properties, dict):
        return f"{path}.properties must be a mapping"
    for name, child in properties.items():
        if not isinstance(name, str) or not name:
            return f"{path}.properties keys must be non-empty strings"
        error = _validate_schema_definition(
            child,
            path=f"{path}.properties.{name}",
        )
        if error:
            return error
    return ""


def _validate_required_declaration(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    if "required" not in schema:
        return ""
    required = schema["required"]
    if not isinstance(required, list) or not all(
        isinstance(item, str) and item for item in required
    ):
        return f"{path}.required must be a list of non-empty strings"
    if len(required) != len(set(required)):
        return f"{path}.required must not contain duplicate property names"
    return ""


def _validate_schema_items(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    if "items" not in schema:
        return ""
    items = schema["items"]
    return _validate_schema_definition(items, path=f"{path}.items")


def _validate_schema_compositions(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    for keyword in ("anyOf", "oneOf", "allOf"):
        if keyword not in schema:
            continue
        candidates = schema[keyword]
        if (
            not isinstance(candidates, list)
            or not candidates
            or not all(isinstance(item, dict) for item in candidates)
        ):
            return f"{path}.{keyword} must be a non-empty list of mappings"
        for index, candidate in enumerate(candidates):
            error = _validate_schema_definition(
                candidate,
                path=f"{path}.{keyword}[{index}]",
            )
            if error:
                return error
    return ""


def _validate_additional_properties_schema(
    schema: dict[str, Any],
    *,
    path: str,
) -> str:
    if "additionalProperties" not in schema:
        return ""
    additional = schema["additionalProperties"]
    if not isinstance(additional, (bool, dict)):
        return f"{path}.additionalProperties must be a boolean or mapping"
    if isinstance(additional, dict):
        return _validate_schema_definition(
            additional,
            path=f"{path}.additionalProperties",
        )
    return ""


__all__ = ["validate_arguments", "validate_schema_definition"]
