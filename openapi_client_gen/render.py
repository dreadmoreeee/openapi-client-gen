"""Small helpers that turn text into ASCII-only Python source."""

from __future__ import annotations

MAX_LINE = 88


def _escape_char(char: str, quote: str) -> str:
    code = ord(char)
    if char == "\\":
        return "\\\\"
    if char == quote:
        return "\\" + quote
    if char == "\n":
        return "\\n"
    if char == "\r":
        return "\\r"
    if char == "\t":
        return "\\t"
    if 32 <= code < 127:
        return char
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def py_str(text: str) -> str:
    """A double-quoted, ASCII-only Python string literal for ``text``."""
    return '"' + "".join(_escape_char(char, '"') for char in text) + '"'


def _doc_line(line: str) -> str:
    out = []
    for char in line:
        if char == '"':
            out.append('"')
        elif char == "\t":
            out.append("    ")
        else:
            out.append(_escape_char(char, ""))
    return "".join(out).replace('"""', '\\"\\"\\"').rstrip()


def docstring(text: str, indent: str) -> list[str]:
    """Lines of a triple-quoted docstring holding ``text``, ASCII-only."""
    raw = [_doc_line(line) for line in text.strip().splitlines()] or [""]
    # Collapse runs of blank lines so the output stays tidy.
    lines: list[str] = []
    for line in raw:
        if line or (lines and lines[-1]):
            lines.append(line)
    if lines[-1].endswith('"'):
        lines[-1] = lines[-1][:-1] + '\\"'
    if len(lines) == 1 and len(indent) + len(lines[0]) + 6 <= MAX_LINE:
        return [f'{indent}"""{lines[0]}"""']
    body = [f"{indent}{line}" if line else "" for line in lines[1:]]
    return [f'{indent}"""{lines[0]}', *body, f'{indent}"""']


def comment(text: str, indent: str = "") -> str:
    """A single ``#`` comment line from the first line of ``text``."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return f"{indent}# {_doc_line(first)}".rstrip()


def call(head: str, args: list[str], indent: str, tail: str = "") -> list[str]:
    """``head(arg, ...)tail``, on one line if it fits, else one arg per line."""
    one = f"{indent}{head}({', '.join(args)}){tail}"
    if len(one) <= MAX_LINE or not args:
        return [one]
    return [f"{indent}{head}(", *[f"{indent}    {arg}," for arg in args], f"{indent}){tail}"]


def dict_literal(target: str, items: list[tuple[str, str]], indent: str) -> list[str]:
    """``target = {key: value, ...}``, wrapped like :func:`call`."""
    pairs = [f"{key}: {value}" for key, value in items]
    one = f"{indent}{target} = {{{', '.join(pairs)}}}"
    if len(one) <= MAX_LINE or not pairs:
        return [one]
    return [f"{indent}{target} = {{", *[f"{indent}    {pair}," for pair in pairs], f"{indent}}}"]
