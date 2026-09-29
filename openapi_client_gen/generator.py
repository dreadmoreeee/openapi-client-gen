"""Generate a single-module, typed httpx client from an OpenAPI 3.x document."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .naming import dedupe, is_valid_class_name, operation_name, pascal_case, snake_case
from .render import MAX_LINE, call, comment, dict_literal, docstring, py_str
from .spec import Resolver, SpecError, check_spec, schema_ref

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
# Header parameters OpenAPI says must be ignored (they are set by the client).
IGNORED_HEADERS = {"accept", "content-type", "authorization"}
PRIMITIVES = {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "null": "None"}
I1, I2, I3 = "    ", "        ", "            "


@dataclass
class Model:
    """A component schema and the Python name it is emitted as."""

    ref: str
    key: str
    name: str
    kind: str  # "dataclass", "literal" or "alias"
    schema: dict[str, Any]


@dataclass
class Arg:
    """One argument of a generated operation method."""

    name: str
    location: str  # "path", "query", "header" or "body"
    wire_name: str
    required: bool
    annotation: str
    description: str = ""


@dataclass
class Operation:
    name: str
    method: str
    path: str
    doc: str
    args: list[Arg] = field(default_factory=list)
    body_kwarg: str = ""
    body_expr: str = ""
    body_media: str = ""
    return_annotation: str = "None"
    return_expr: str = ""


@dataclass
class AuthOption:
    kind: str  # "bearer", "basic", "header" or "query"
    name: str
    wire_name: str
    doc: str


def json_like(media: str) -> bool:
    media = media.split(";")[0].strip().lower()
    return media == "application/json" or media.endswith("+json") or media == "*/*"


def schema_types(schema: dict[str, Any]) -> tuple[list[str], bool]:
    """The non-null JSON types of ``schema`` and whether it allows null."""
    raw = schema.get("type")
    if isinstance(raw, list):
        types = [str(item) for item in raw if item != "null"]
        nullable = "null" in raw
    elif isinstance(raw, str):
        types = [] if raw == "null" else [raw]
        nullable = raw == "null"
    else:
        types, nullable = [], False
    if schema.get("nullable") is True or None in (schema.get("enum") or []):
        nullable = True
    return types, nullable


def add_none(annotation: str) -> str:
    if annotation in ("Any", "None") or annotation.endswith("| None"):
        return annotation
    return f"{annotation} | None"


def _literal_value(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return py_str(value)
    return None


class Generator:
    """Builds the client source. Use :func:`generate` for the simple case."""

    def __init__(self, spec: dict[str, Any], class_name: str) -> None:
        check_spec(spec)
        if not is_valid_class_name(class_name):
            raise SpecError(f"--class-name must be an ASCII Python identifier, got {class_name!r}")
        self.spec = spec
        self.resolver = Resolver(spec)
        self.class_name = class_name
        self.error_name = f"{class_name}Error"
        self.warnings: list[str] = []
        self.uses_literal = False
        self.models: dict[str, Model] = {}
        self._taken_names = {class_name, self.error_name, "Any", "Literal", "Exception"}
        self._collect_models()
        self.api_error_alias = "ApiError" not in self._taken_names

    # -- models ---------------------------------------------------------

    def _collect_models(self) -> None:
        components = self.spec.get("components") or {}
        for key, schema in (components.get("schemas") or {}).items():
            key = str(key)
            schema = schema if isinstance(schema, dict) else {}
            name = dedupe(pascal_case(key), self._taken_names, hint="Model", sep="")
            ref = schema_ref(key)
            self.models[ref] = Model(ref, key, name, self._kind(schema), schema)

    def _kind(self, schema: dict[str, Any]) -> str:
        if "$ref" in schema:
            return "alias"
        if "enum" in schema:
            values = [value for value in schema["enum"] or [] if value is not None]
            if values and all(_literal_value(value) is not None for value in values):
                return "literal"
            return "alias"
        properties, _ = self.merge_object(schema)
        return "dataclass" if properties else "alias"

    def merge_object(
        self, schema: dict[str, Any], seen: frozenset[str] = frozenset()
    ) -> tuple[dict[str, Any], set[str]]:
        """Properties and required names of an object, flattening ``allOf``."""
        properties: dict[str, Any] = {}
        required: set[str] = set()
        for part in schema.get("allOf") or []:
            ref = part.get("$ref") if isinstance(part, dict) else None
            if ref in seen:
                continue
            target = self.resolver.resolve(part)
            if isinstance(target, dict):
                sub_props, sub_required = self.merge_object(target, seen | {ref or ""})
                properties.update(sub_props)
                required |= sub_required
        properties.update(schema.get("properties") or {})
        required |= {str(name) for name in schema.get("required") or []}
        return properties, required

    # -- type mapping ---------------------------------------------------

    def annotation(self, schema: Any, seen: tuple[str, ...] = ()) -> str:
        """The Python type annotation for a schema."""
        if not isinstance(schema, dict) or not schema:
            return "Any"
        if "$ref" in schema:
            ref = schema["$ref"]
            model = self.models.get(ref)
            if model is not None:
                return model.name
            if ref in seen:
                return "Any"
            return self.annotation(self.resolver.lookup(ref), seen + (ref,))
        types, nullable = schema_types(schema)
        if nullable and not types and schema.get("type") is not None:
            return "None"  # type: "null" or ["null"]
        base = self._base_annotation(schema, types, seen)
        return add_none(base) if nullable else base

    def _base_annotation(self, schema: dict[str, Any], types: list[str], seen: tuple[str, ...]) -> str:
        if "enum" in schema:
            values = [_literal_value(v) for v in schema["enum"] or [] if v is not None]
            if values and None not in values:
                self.uses_literal = True
                return f"Literal[{', '.join(dict.fromkeys(values))}]"
        if "oneOf" in schema or "anyOf" in schema:
            return "Any"
        all_of = schema.get("allOf")
        if all_of and len(all_of) == 1 and not schema.get("properties"):
            return self.annotation(all_of[0], seen)
        if len(types) > 1:
            return " | ".join(dict.fromkeys(self._simple(t, schema, seen) for t in types))
        if types:
            return self._simple(types[0], schema, seen)
        if "properties" in schema or "additionalProperties" in schema or all_of:
            return self._simple("object", schema, seen)
        if "items" in schema:
            return self._simple("array", schema, seen)
        return "Any"

    def _simple(self, kind: str, schema: dict[str, Any], seen: tuple[str, ...]) -> str:
        if kind in PRIMITIVES:
            return PRIMITIVES[kind]
        if kind == "array":
            return f"list[{self.annotation(schema.get('items'), seen)}]"
        if kind == "object":
            extra = schema.get("additionalProperties")
            if isinstance(extra, dict) and extra and not schema.get("properties"):
                return f"dict[str, {self.annotation(extra, seen)}]"
            return "dict[str, Any]"
        return "Any"

    def convert(self, schema: Any, expr: str, mode: str, depth: int = 0,
                seen: tuple[str, ...] = (), guard: bool = True) -> str:
        """Expression turning ``expr`` from JSON into Python (``mode="decode"``)
        or from Python into JSON (``mode="encode"``). Returns ``expr`` itself
        when no conversion is needed."""
        if not isinstance(schema, dict) or not schema:
            return expr
        if "$ref" in schema:
            ref = schema["$ref"]
            model = self.models.get(ref)
            if model is not None and model.kind == "dataclass":
                return f"{model.name}.from_dict({expr})" if mode == "decode" else f"{expr}.to_dict()"
            if ref in seen:
                return expr
            return self.convert(self.resolver.lookup(ref), expr, mode, depth, seen + (ref,), guard)
        types, nullable = schema_types(schema)
        all_of = schema.get("allOf")
        extra = schema.get("additionalProperties")
        if "oneOf" in schema or "anyOf" in schema:
            inner = expr
        elif all_of and len(all_of) == 1 and not schema.get("properties"):
            inner = self.convert(all_of[0], expr, mode, depth, seen, guard=False)
        elif types == ["array"] or (not types and "items" in schema):
            var = f"v{depth}"
            item = self.convert(schema.get("items"), var, mode, depth + 1, seen)
            inner = expr if item == var else f"[{item} for {var} in {expr}]"
        elif (types in (["object"], [])) and isinstance(extra, dict) and extra \
                and not schema.get("properties"):
            key, var = f"k{depth}", f"v{depth}"
            item = self.convert(extra, var, mode, depth + 1, seen)
            inner = expr if item == var else f"{{{key}: {item} for {key}, {var} in {expr}.items()}}"
        else:
            inner = expr
        if guard and nullable and inner != expr:
            inner = f"None if {expr} is None else {inner}"
        return inner

    # -- operations -----------------------------------------------------

    def operations(self) -> list[Operation]:
        taken = {"close"}
        result = []
        for path, item in (self.spec.get("paths") or {}).items():
            item = self.resolver.resolve(item)
            if not isinstance(item, dict):
                continue
            shared = item.get("parameters") or []
            for method, op in item.items():
                if str(method).lower() not in HTTP_METHODS or not isinstance(op, dict):
                    continue
                name = dedupe(operation_name(str(method), str(path), op.get("operationId")), taken)
                result.append(self._operation(name, str(method).upper(), str(path), op, shared))
        return result

    def _operation(self, name: str, method: str, path: str, op: dict[str, Any],
                   shared: list[Any]) -> Operation:
        label = f"{method} {path}"
        params: dict[tuple[str, str], dict[str, Any]] = {}
        for raw in [*shared, *(op.get("parameters") or [])]:
            param = self.resolver.resolve(raw)
            if not isinstance(param, dict) or "name" not in param or "in" not in param:
                self.warnings.append(f"{label}: skipped a parameter without name/in")
                continue
            params[(str(param["in"]), str(param["name"]))] = param
        taken = {"self", "params", "headers"}  # names used as locals before the request
        args: list[Arg] = []
        in_template = re.findall(r"\{([^}]+)\}", path)
        for wire in in_template:
            param = params.get(("path", wire))
            if param is None:
                self.warnings.append(f"{label}: path parameter {wire!r} is not declared; using str")
                param = {"name": wire, "in": "path", "schema": {"type": "string"}}
            args.append(self._arg(param, "path", True, taken))
        doc = self._op_doc(method, path, op)
        result = Operation(name, method, path, doc)
        body = self._body(op, taken, label, result)
        if body is not None:
            args.append(body)
        for location in ("query", "header"):
            for (where, wire), param in params.items():
                if where != location or (where == "header" and wire.lower() in IGNORED_HEADERS):
                    continue
                args.append(self._arg(param, location, param.get("required") is True, taken))
        for (where, wire) in params:
            if where == "path" and wire not in in_template:
                self.warnings.append(f"{label}: path parameter {wire!r} is not in the path; skipped")
            elif where not in ("path", "query", "header"):
                self.warnings.append(f"{label}: {where} parameter {wire!r} is not supported; skipped")
        result.args = args
        self._response(op, result)
        return result

    def _arg(self, param: dict[str, Any], location: str, required: bool, taken: set[str]) -> Arg:
        wire = str(param["name"])
        schema = param.get("schema")
        if schema is None and isinstance(param.get("content"), dict) and param["content"]:
            schema = next(iter(param["content"].values())).get("schema")
        annotation = self.annotation(schema)
        name = dedupe(snake_case(wire, fallback="param"), taken, hint=location)
        return Arg(name, location, wire, required, annotation, str(param.get("description") or ""))

    def _body(self, op: dict[str, Any], taken: set[str], label: str, result: Operation) -> Arg | None:
        body = self.resolver.resolve(op.get("requestBody"))
        if not isinstance(body, dict) or not body.get("content"):
            return None
        content = body["content"]
        media = next((m for m in content if json_like(m)), None)
        media = media or next(iter(content))
        schema = (content.get(media) or {}).get("schema")
        required = body.get("required") is True
        name = dedupe("body", taken, hint="")
        description = str(body.get("description") or "")
        lower = media.split(";")[0].strip().lower()
        if json_like(media):
            annotation = self.annotation(schema)
            expr = self.convert(schema, name, "encode")
            if not required and expr != name:
                expr = f"None if {name} is None else {expr}"
            result.body_kwarg, result.body_expr = "json", expr
        elif lower == "application/x-www-form-urlencoded":
            annotation = "dict[str, Any]"
            result.body_kwarg, result.body_expr = "data", name
        elif lower == "multipart/form-data":
            annotation = "dict[str, Any]"
            result.body_kwarg, result.body_expr = "files", name
            description = (description + "\n" if description else "") + \
                "Sent as multipart/form-data through httpx ``files=``."
        else:
            annotation = "bytes | str"
            result.body_kwarg, result.body_expr = "content", name
            result.body_media = "application/octet-stream" if "*" in lower else media
        if len(content) > 1:
            self.warnings.append(f"{label}: request body has several media types; using {media}")
        return Arg(name, "body", media, required, annotation, description)

    def _response(self, op: dict[str, Any], result: Operation) -> None:
        responses = {str(code): value for code, value in (op.get("responses") or {}).items()}
        success = sorted(c for c in responses if len(c) == 3 and c.isdigit() and c[0] == "2")
        success += [c for c in responses if c.upper() == "2XX"]
        if not success:
            result.return_annotation = "Any"
            result.return_expr = "_response_body(response)"
            return
        response = self.resolver.resolve(responses[success[0]])
        content = response.get("content") if isinstance(response, dict) else None
        if not content:
            return
        media = next((m for m in content if json_like(m)), None) or next(iter(content))
        if json_like(media):
            schema = (content.get(media) or {}).get("schema")
            result.return_annotation = self.annotation(schema)
            result.return_expr = self.convert(schema, "data", "decode")
        elif media.lower().startswith("text/"):
            result.return_annotation, result.return_expr = "str", "response.text"
        else:
            result.return_annotation, result.return_expr = "bytes", "response.content"

    @staticmethod
    def _op_doc(method: str, path: str, op: dict[str, Any]) -> str:
        parts = []
        summary = str(op.get("summary") or "").strip()
        description = str(op.get("description") or "").strip()
        first = summary or description.split("\n")[0] or f"Call {method} {path}"
        if first[-1].isalnum() or first[-1] in ")]'`\"":
            first += "."
        parts.append(first)
        parts.append(f"``{method} {path}``")
        if description and description != parts[0]:
            parts.append(description)
        if op.get("deprecated") is True:
            parts.append("Deprecated by the API.")
        return "\n\n".join(parts)

    # -- auth -----------------------------------------------------------

    def auth_options(self) -> list[AuthOption]:
        schemes = (self.spec.get("components") or {}).get("securitySchemes") or {}
        resolved = {str(k): self.resolver.resolve(v) for k, v in schemes.items()}
        api_keys = [k for k, s in resolved.items()
                    if isinstance(s, dict) and s.get("type") == "apiKey" and s.get("in") in ("header", "query")]
        taken = {"base_url", "timeout", "headers", "client", "transport", "self"}
        options: list[AuthOption] = []
        for key, scheme in resolved.items():
            if not isinstance(scheme, dict):
                continue
            kind = scheme.get("type")
            http_scheme = str(scheme.get("scheme") or "").lower()
            if (kind == "http" and http_scheme == "bearer") or kind in ("oauth2", "openIdConnect"):
                if not any(o.kind == "bearer" for o in options):
                    name = dedupe("token", taken)
                    options.append(AuthOption("bearer", name, "Authorization",
                                              "Bearer token, sent as ``Authorization: Bearer <token>``."))
            elif kind == "http" and http_scheme == "basic":
                if not any(o.kind == "basic" for o in options):
                    name = dedupe("basic_auth", taken)
                    options.append(AuthOption("basic", name, "Authorization",
                                              "``(username, password)`` for HTTP basic auth."))
            elif kind == "apiKey" and key in api_keys:
                where = str(scheme["in"])
                wire = str(scheme.get("name") or key)
                name = dedupe("api_key" if len(api_keys) == 1 else snake_case(key), taken)
                place = "header" if where == "header" else "query parameter"
                options.append(AuthOption(where, name, wire, f"API key, sent in the ``{wire}`` {place}."))
            else:
                self.warnings.append(f"security scheme {key!r} ({kind}) is not supported; skipped")
        return options

    # -- emitting -------------------------------------------------------

    def _alias_order(self) -> list[Model]:
        aliases = [m for m in self.models.values() if m.kind == "alias"]
        by_ref = {m.ref: m for m in aliases}
        ordered: list[Model] = []
        state: dict[str, str] = {}

        def refs(node: Any) -> list[str]:
            if isinstance(node, dict):
                found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
                for value in node.values():
                    found += refs(value)
                return found
            if isinstance(node, list):
                return [ref for value in node for ref in refs(value)]
            return []

        def visit(model: Model) -> None:
            if state.get(model.ref) == "done":
                return
            if state.get(model.ref) == "active":
                self.warnings.append(f"schema {model.key!r} refers to itself through aliases; typed as Any")
                model.kind = "broken"
                return
            state[model.ref] = "active"
            for ref in refs(model.schema):
                if ref in by_ref:
                    visit(by_ref[ref])
            state[model.ref] = "done"
            ordered.append(model)

        for model in aliases:
            visit(model)
        return ordered

    def _emit_dataclass(self, model: Model) -> list[str]:
        properties, required = self.merge_object(model.schema)
        taken = {"self", "to_dict", "from_dict"}
        fields = []
        for json_name, schema in properties.items():
            schema = schema if isinstance(schema, dict) else {}
            name = dedupe(snake_case(str(json_name), fallback="field"), taken)
            is_required = str(json_name) in required
            annotation = self.annotation(schema)
            if not is_required:
                annotation = add_none(annotation)
            description = str(schema.get("description") or schema.get("title") or "")
            fields.append((str(json_name), name, schema, is_required, annotation, description))
        fields.sort(key=lambda f: not f[3])  # stable: required fields first

        doc = str(model.schema.get("description") or model.schema.get("title") or f"The {model.key} schema.")
        described = [f for f in fields if f[5]]
        if described:
            doc += "\n\nAttributes:\n" + "\n".join(
                f"    {f[1]}: {f[5].strip().splitlines()[0]}" for f in described)
        out = ["@dataclass", f"class {model.name}:", *docstring(doc, I1), ""]
        for _, name, _, is_required, annotation, _ in fields:
            out.append(f"{I1}{name}: {annotation}" + ("" if is_required else " = None"))

        out += ["", f"{I1}@classmethod", f"{I1}def from_dict(cls, data: dict[str, Any]) -> {model.name}:",
                f'{I2}"""Build an instance from a decoded JSON object."""', f"{I2}return cls("]
        for json_name, name, schema, is_required, _, _ in fields:
            key = py_str(json_name)
            if is_required:
                value = self.convert(schema, f"data[{key}]", "decode")
            else:
                inner = self.convert(schema, f"data[{key}]", "decode", guard=False)
                if inner == f"data[{key}]":
                    value = f"data.get({key})"
                else:
                    value = f"None if data.get({key}) is None else {inner}"
                    if len(f"{I3}{name}={value},") > MAX_LINE:
                        out += [f"{I3}{name}=(", f"{I3}    None", f"{I3}    if data.get({key}) is None",
                                f"{I3}    else {inner}", f"{I3}),"]
                        continue
            out.append(f"{I3}{name}={value},")
        out.append(f"{I2})")

        out += ["", f"{I1}def to_dict(self) -> dict[str, Any]:",
                f'{I2}"""Return a JSON-ready dict; unset optional fields are left out."""']
        items = [(py_str(j), self.convert(s, f"self.{n}", "encode")) for j, n, s, r, _, _ in fields if r]
        out += dict_literal("result: dict[str, Any]", items, I2)
        for json_name, name, schema, is_required, _, _ in fields:
            if is_required:
                continue
            value = self.convert(schema, f"self.{name}", "encode", guard=False)
            out += [f"{I2}if self.{name} is not None:", f"{I3}result[{py_str(json_name)}] = {value}"]
        out.append(f"{I2}return result")
        return out

    def _emit_operation(self, op: Operation) -> list[str]:
        positional = [a for a in op.args if a.location == "path" or (a.location == "body" and a.required)]
        keyword = [a for a in op.args if a not in positional]
        keyword.sort(key=lambda a: not a.required)
        params = ["self"]
        params += [f"{a.name}: {a.annotation}" for a in positional]
        if keyword:
            params.append("*")
        for a in keyword:
            params.append(f"{a.name}: {a.annotation}" if a.required else f"{a.name}: {add_none(a.annotation)} = None")
        out = call(f"def {op.name}", params, I1, f" -> {op.return_annotation}:")

        doc = op.doc
        described = [a for a in op.args if a.description.strip()]
        if described:
            lines = []
            for a in described:
                first, *rest = a.description.strip().splitlines()
                lines.append(f"    {a.name}: {first}")
                lines += [f"        {line}" if line.strip() else "" for line in rest]
            doc += "\n\nArgs:\n" + "\n".join(lines)
        out += docstring(doc, I2)

        query = [(py_str(a.wire_name), a.name) for a in op.args if a.location == "query"]
        headers = [(py_str(a.wire_name), a.name) for a in op.args if a.location == "header"]
        if op.body_media:
            headers.append(('"Content-Type"', py_str(op.body_media)))
        request_args = [py_str(op.method), self._path_expr(op)]
        if query:
            out += dict_literal("params", query, I2)
            request_args.append("params=params")
        if headers:
            out += dict_literal("headers", headers, I2)
            request_args.append("headers=headers")
        if op.body_kwarg:
            request_args.append(f"{op.body_kwarg}={op.body_expr}")
        if op.return_expr:
            out += call("response = self._request", request_args, I2)
            uses = re.findall(r"\w+", op.return_expr).count("data")
            if uses == 1:
                inline = re.sub(r"\bdata\b", "response.json()", op.return_expr)
                out.append(f"{I2}return {inline}")
            elif uses > 1:
                out += [f"{I2}data = response.json()", f"{I2}return {op.return_expr}"]
            else:
                out.append(f"{I2}return {op.return_expr}")
        else:
            out += call("self._request", request_args, I2)
        return out

    @staticmethod
    def _path_expr(op: Operation) -> str:
        names = {a.wire_name: a.name for a in op.args if a.location == "path"}
        pieces = re.split(r"(\{[^}]+\})", op.path)
        if len(pieces) == 1:
            return py_str(op.path)
        out = []
        for piece in pieces:
            if piece.startswith("{") and piece.endswith("}") and piece[1:-1] in names:
                out.append("{_path(" + names[piece[1:-1]] + ")}")
            else:
                out.append(py_str(piece)[1:-1].replace("{", "{{").replace("}", "}}"))
        return 'f"' + "".join(out) + '"'

    def _default_base_url(self) -> str | None:
        servers = self.spec.get("servers") or []
        if not servers or not isinstance(servers[0], dict) or not servers[0].get("url"):
            return None
        url = str(servers[0]["url"])
        for var, spec in (servers[0].get("variables") or {}).items():
            if isinstance(spec, dict) and "default" in spec:
                url = url.replace("{" + str(var) + "}", str(spec["default"]))
        if not re.match(r"^https?://", url) or "{" in url:
            return None
        return url.rstrip("/")

    def _emit_client(self, ops: list[Operation], auth: list[AuthOption]) -> list[str]:
        info = self.spec.get("info") or {}
        title = str(info.get("title") or "API")
        version = str(info.get("version") or "")
        doc = f"Client for {title}" + (f" {version}" if version else "") + "."
        if info.get("description"):
            doc += "\n\n" + str(info["description"]).strip()
        doc += f"\n\nEvery method raises :class:`{self.error_name}` on a non-2xx response."
        out = [f"class {self.class_name}:", *docstring(doc, I1), ""]

        base_url = self._default_base_url()
        params = ["self", "base_url: str" + (f" = {py_str(base_url)}" if base_url else ""), "*"]
        params += [f"{o.name}: tuple[str, str] | None = None" if o.kind == "basic"
                   else f"{o.name}: str | None = None" for o in auth]
        params += ["timeout: float = 30.0", "headers: dict[str, str] | None = None",
                   "client: httpx.Client | None = None", "transport: httpx.BaseTransport | None = None"]
        out += call("def __init__", params, I1, " -> None:")
        arg_docs = ["base_url: Server root URL; operation paths are appended to it."]
        arg_docs += [f"{o.name}: {o.doc}" for o in auth]
        arg_docs += [
            "timeout: Request timeout in seconds (only for the client created here).",
            "headers: Extra headers sent with every request.",
            "client: An ``httpx.Client`` to use instead of creating one; it is not",
            "    closed by :meth:`close`.",
            "transport: An httpx transport for the created client, e.g.",
            "    ``httpx.MockTransport`` in tests.",
        ]
        out += docstring("Create the client.\n\nArgs:\n" + "\n".join("    " + d for d in arg_docs), I2)
        out += [
            f"{I2}if client is not None and transport is not None:",
            f'{I3}raise ValueError("pass either client or transport, not both")',
            f'{I2}self._base_url = base_url.rstrip("/")',
            f"{I2}self._headers: dict[str, str] = dict(headers or {{}})",
            f"{I2}self._params: dict[str, str] = {{}}",
        ]
        has_basic = any(o.kind == "basic" for o in auth)
        if has_basic:
            out.append(f"{I2}self._auth: httpx.Auth | None = None")
        for o in auth:
            out.append(f"{I2}if {o.name} is not None:")
            if o.kind == "bearer":
                out.append(f'{I3}self._headers["Authorization"] = f"Bearer {{{o.name}}}"')
            elif o.kind == "basic":
                out.append(f"{I3}self._auth = httpx.BasicAuth(*{o.name})")
            elif o.kind == "header":
                out.append(f"{I3}self._headers[{py_str(o.wire_name)}] = {o.name}")
            else:
                out.append(f"{I3}self._params[{py_str(o.wire_name)}] = {o.name}")
        out += [
            f"{I2}self._owns_client = client is None",
            f"{I2}if client is None:",
            f"{I3}client = httpx.Client(timeout=timeout, transport=transport)",
            f"{I2}self._client = client",
            "",
            f"{I1}def __enter__(self) -> {self.class_name}:",
            f"{I2}return self",
            "",
            f"{I1}def __exit__(self, *exc_info: object) -> None:",
            f"{I2}self.close()",
            "",
            f"{I1}def close(self) -> None:",
            f'{I2}"""Close the HTTP client, unless it was passed in by the caller."""',
            f"{I2}if self._owns_client:",
            f"{I3}self._client.close()",
            "",
        ]
        out += call("def _request", ["self", "method: str", "path: str", "*",
                                     "params: dict[str, Any] | None = None",
                                     "headers: dict[str, Any] | None = None",
                                     "**kwargs: Any"], I1, " -> httpx.Response:")
        out += [
            f"{I2}query: dict[str, Any] = dict(self._params)",
            f"{I2}for key, value in (params or {{}}).items():",
            f"{I3}if value is not None:",
            f"{I3}    query[key] = value",
            f"{I2}merged = dict(self._headers)",
            f"{I2}for key, value in (headers or {{}}).items():",
            f"{I3}if value is not None:",
            f"{I3}    merged[key] = _header_value(value)",
        ]
        if has_basic:
            out += [f"{I2}if self._auth is not None:", f'{I3}kwargs["auth"] = self._auth']
        out += [
            f"{I2}url = self._base_url + path",
            *call("response = self._client.request",
                  ["method", "url", "params=query", "headers=merged", "**kwargs"], I2),
            f"{I2}if not response.is_success:",
            *call(f"raise {self.error_name}", ["method", "url", "response.status_code",
                                               "_response_body(response)", "response"], I3),
            f"{I2}return response",
        ]
        for op in ops:
            out += ["", *self._emit_operation(op)]
        return out

    def _emit_helpers(self) -> list[str]:
        return [
            "def _path(value: Any) -> str:",
            f'{I1}"""Format and percent-encode a path parameter."""',
            f"{I1}if isinstance(value, bool):",
            f'{I2}return "true" if value else "false"',
            f'{I1}return quote(str(value), safe="")',
            "",
            "",
            "def _header_value(value: Any) -> str:",
            f'{I1}"""Format a header parameter value."""',
            f"{I1}if isinstance(value, bool):",
            f'{I2}return "true" if value else "false"',
            f"{I1}if isinstance(value, (list, tuple)):",
            f'{I2}return ",".join(_header_value(item) for item in value)',
            f"{I1}return str(value)",
            "",
            "",
            "def _response_body(response: httpx.Response) -> Any:",
            f'{I1}"""The decoded JSON body, or the raw text when it is not JSON."""',
            f"{I1}try:",
            f"{I2}return response.json()",
            f"{I1}except ValueError:",
            f"{I2}return response.text",
            "",
            "",
            f"class {self.error_name}(Exception):",
            *docstring(
                "Raised when the API answers with a non-2xx status code.\n\n"
                "Attributes:\n"
                "    method: HTTP method of the request.\n"
                "    url: Request URL, without the query string.\n"
                "    status_code: HTTP status code of the response.\n"
                "    body: Decoded JSON body, or the raw text when it is not JSON.\n"
                "    response: The ``httpx.Response`` itself.", I1),
            "",
            *call("def __init__", ["self", "method: str", "url: str", "status_code: int", "body: Any",
                                   "response: httpx.Response | None = None"], I1, " -> None:"),
            f"{I2}detail = repr(body)",
            f"{I2}if len(detail) > 200:",
            f'{I3}detail = detail[:197] + "..."',
            f'{I2}super().__init__(f"{{method}} {{url}} returned HTTP {{status_code}}: {{detail}}")',
            f"{I2}self.method = method",
            f"{I2}self.url = url",
            f"{I2}self.status_code = status_code",
            f"{I2}self.body = body",
            f"{I2}self.response = response",
        ]

    def generate(self) -> str:
        auth = self.auth_options()
        ops = self.operations()
        aliases = self._alias_order()
        literals = [m for m in self.models.values() if m.kind == "literal"]
        classes = [m for m in self.models.values() if m.kind == "dataclass"]

        blocks: list[list[str]] = []
        type_lines: list[str] = []
        for model in literals:
            if model.schema.get("description"):
                type_lines.append(comment(str(model.schema["description"])))
            type_lines.append(f"{model.name} = {self.annotation(model.schema)}")
        if type_lines:
            blocks.append(type_lines)
        blocks += [self._emit_dataclass(m) for m in classes]
        alias_lines: list[str] = []
        for model in aliases:
            if model.kind == "broken":
                alias_lines.append(f"{model.name} = Any")
                continue
            if model.schema.get("description"):
                alias_lines.append(comment(str(model.schema["description"])))
            alias_lines.append(f"{model.name} = {self.annotation(model.schema)}")
        if alias_lines:
            blocks.append(alias_lines)
        blocks.append(self._emit_helpers())
        if self.api_error_alias:
            blocks.append([f"ApiError = {self.error_name}"])
        blocks.append(self._emit_client(ops, auth))

        info = self.spec.get("info") or {}
        title = str(info.get("title") or "API")
        version = f" (version {info['version']})" if info.get("version") else ""
        header_doc = (
            f"Typed Python client for {title}{version}.\n\n"
            f"Generated by openapi-client-gen from an OpenAPI {self.spec.get('openapi')} document.\n"
            "Edit the spec and regenerate instead of editing this file."
        )
        exported = [self.class_name, self.error_name] + (["ApiError"] if self.api_error_alias else [])
        exported += [m.name for m in literals + classes + aliases]
        typing_names = "Any, Literal" if self.uses_literal else "Any"
        header = [
            *docstring(header_doc, ""),
            "",
            "from __future__ import annotations",
            "",
            *(["from dataclasses import dataclass"] if classes else []),
            f"from typing import {typing_names}",
            "from urllib.parse import quote",
            "",
            "import httpx",
            "",
            "__all__ = [",
            *[f"    {py_str(name)}," for name in exported],
            "]",
        ]
        lines = header
        for block in blocks:
            lines += ["", "", *block]
        source = "\n".join(line.rstrip() for line in lines) + "\n"
        if not source.isascii():  # pragma: no cover - guarded by the escaping helpers
            raise AssertionError("generated source is not ASCII")
        compile(source, "<generated client>", "exec")
        return source


def generate(spec: dict[str, Any], class_name: str = "ApiClient") -> str:
    """Return the client module source for ``spec``."""
    return Generator(spec, class_name).generate()
