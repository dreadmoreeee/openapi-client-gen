"""Turn arbitrary OpenAPI names into safe, readable Python identifiers."""

from __future__ import annotations

import keyword
import re
import unicodedata

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")
_PATH_PARAM = re.compile(r"^\{([^}]*)\}$")


def split_words(text: str) -> list[str]:
    """Split ``text`` into words on case changes and punctuation.

    >>> split_words("getPetByID")
    ['get', 'Pet', 'By', 'ID']
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = _ACRONYM.sub(r"\1 \2", text)
    text = _CAMEL.sub(r"\1 \2", text)
    return [word for word in _NON_WORD.split(text) if word]


def _make_safe(name: str) -> str:
    if keyword.iskeyword(name):
        return name + "_"
    return name


def snake_case(text: str, fallback: str = "value") -> str:
    """``listPets`` -> ``list_pets``; keywords get a trailing underscore."""
    name = "_".join(word.lower() for word in split_words(text)) or fallback
    if name[0].isdigit():
        name = "x_" + name
    return _make_safe(name)


def pascal_case(text: str, fallback: str = "Model") -> str:
    """``pet_status`` -> ``PetStatus``."""
    name = "".join(word[0].upper() + word[1:] for word in split_words(text)) or fallback
    if name[0].isdigit():
        name = fallback + name
    return _make_safe(name)


def operation_name(method: str, path: str, operation_id: str | None) -> str:
    """Method name for an operation: operationId, else method + path.

    >>> operation_name("get", "/pets/{petId}", None)
    'get_pets_by_pet_id'
    """
    if operation_id:
        return snake_case(operation_id, fallback=method.lower())
    parts = [method.lower()]
    for segment in path.split("/"):
        if not segment:
            continue
        match = _PATH_PARAM.match(segment)
        if match:
            parts.append("by_" + snake_case(match.group(1)).rstrip("_"))
        else:
            parts.append(snake_case(segment).rstrip("_"))
    return snake_case("_".join(parts))


def dedupe(name: str, taken: set[str], hint: str = "", sep: str = "_") -> str:
    """Return ``name`` or a variant not in ``taken``, and reserve it.

    Tries ``name``, then ``name + sep + hint`` (when a hint is given), then
    numbered variants ``name + sep + 2``, ``name + sep + 3`` and so on.
    """
    base = name.rstrip("_") or name  # "from_" + "query" -> "from_query"
    candidates = [name]
    if hint:
        candidates.append(f"{base}{sep}{hint}")
    for candidate in candidates:
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    number = 2
    while f"{base}{sep}{number}" in taken:
        number += 1
    result = f"{base}{sep}{number}"
    taken.add(result)
    return result


def is_valid_class_name(name: str) -> bool:
    """True if ``name`` can be used as a generated class name."""
    return name.isascii() and name.isidentifier() and not keyword.iskeyword(name)
