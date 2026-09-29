"""Request/response variants, auth schemes, CLI behaviour and output hygiene."""

from __future__ import annotations

import inspect
import json

import httpx
import pytest
import yaml

from openapi_client_gen import Generator, SpecError, generate, load_spec
from openapi_client_gen.cli import main

OK = {"204": {"description": "ok"}}

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Misc API", "version": "2", "description": "Caf\u00e9 \"quotes\" and \\ back\\slash"},
    "servers": [{"url": "https://{region}.example.com/api", "variables": {"region": {"default": "eu"}}}],
    "paths": {
        "/form": {"post": {"operationId": "sendForm", "requestBody": {"required": True, "content": {
            "application/x-www-form-urlencoded": {"schema": {"type": "object"}}}}, "responses": OK}},
        "/raw": {"put": {"operationId": "upload", "requestBody": {"required": True, "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
            "responses": {"200": {"description": "ok", "content": {"text/plain": {"schema": {"type": "string"}}}}}}},
        "/multi": {"post": {"operationId": "sendFiles", "requestBody": {"required": True, "content": {
            "multipart/form-data": {"schema": {"type": "object"}}}}, "responses": OK}},
        "/blob": {"get": {"operationId": "download", "deprecated": True, "responses": {
            "200": {"description": "ok", "content": {"image/png": {}}}}}},
        "/search": {"get": {
            "operationId": "search",
            "description": "Search things.\n\nLonger text with \"\"\"triple quotes\"\"\" inside.",
            "parameters": [
                {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}},
                {"name": "tags", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}},
                {"name": "X-Debug", "in": "header", "schema": {"type": "boolean"}},
                {"name": "Accept", "in": "header", "schema": {"type": "string"}},
                {"name": "session", "in": "cookie", "schema": {"type": "string"}},
            ],
            "responses": {"default": {"description": "anything"}}}},
    },
    "components": {"securitySchemes": {
        "basic": {"type": "http", "scheme": "basic"},
        "queryKey": {"type": "apiKey", "in": "query", "name": "key"},
        "headerKey": {"type": "apiKey", "in": "header", "name": "X-Key"},
        "oauth": {"type": "oauth2", "flows": {}},
        "cookieKey": {"type": "apiKey", "in": "cookie", "name": "sid"},
    }},
}


@pytest.fixture
def misc(load_generated):
    return load_generated(generate(SPEC, "Misc"))


def _client(mod, handler, **kwargs):
    return mod.Misc(transport=httpx.MockTransport(handler), **kwargs)


def test_server_variables_default_base_url(misc):
    assert inspect.signature(misc.Misc).parameters["base_url"].default == "https://eu.example.com/api"


def test_auth_options_from_security_schemes(misc):
    seen = []
    with _client(misc, lambda r: seen.append(r) or httpx.Response(204),
                 basic_auth=("u", "p"), query_key="qk", header_key="hk") as client:
        client.send_form({"a": "1"})
    request = seen[0]
    assert request.url.params["key"] == "qk"
    assert request.headers["x-key"] == "hk"
    assert request.headers["authorization"] == "Basic dTpw"
    with _client(misc, lambda r: seen.append(r) or httpx.Response(204), token="t") as client:
        client.send_form({})
    assert seen[1].headers["authorization"] == "Bearer t"  # oauth2 -> bearer token
    params = inspect.signature(misc.Misc).parameters
    assert "cookie_key" not in params and "sid" not in params


def test_form_raw_and_multipart_bodies(misc):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/raw"):
            return httpx.Response(200, text="stored")
        return httpx.Response(204)

    with _client(misc, handler) as client:
        assert client.send_form({"a": "1", "b": "two"}) is None
        assert client.upload(b"\x00\x01") == "stored"
        client.send_files({"file": ("a.txt", b"hello", "text/plain")})
    assert seen[0].headers["content-type"] == "application/x-www-form-urlencoded"
    assert seen[0].content == b"a=1&b=two"
    assert seen[1].headers["content-type"] == "application/octet-stream"
    assert seen[1].content == b"\x00\x01"
    assert seen[2].headers["content-type"].startswith("multipart/form-data")
    assert b"hello" in seen[2].read()


def test_binary_response_and_default_response(misc):
    def handler(request):
        if request.url.path.endswith("/blob"):
            return httpx.Response(200, content=b"\x89PNG")
        return httpx.Response(200, json={"hits": 1})

    with _client(misc, handler) as client:
        assert client.download() == b"\x89PNG"
        assert client.search(q="x") == {"hits": 1}
    assert "Deprecated by the API." in misc.Misc.download.__doc__
    assert inspect.signature(misc.Misc.download).return_annotation == "bytes"


def test_query_and_header_params(misc):
    seen = []
    with _client(misc, lambda r: seen.append(r) or httpx.Response(200, text="plain")) as client:
        assert client.search(q="a b", tags=["x", "y"], x_debug=True) == "plain"
        with pytest.raises(TypeError):
            client.search()  # q is a required keyword-only argument
    assert seen[0].url.params.multi_items() == [("q", "a b"), ("tags", "x"), ("tags", "y")]
    assert seen[0].headers["x-debug"] == "true"
    sig = inspect.signature(misc.Misc.search)
    assert sig.parameters["q"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["q"].default is inspect.Parameter.empty
    assert "accept" not in sig.parameters and "session" not in sig.parameters


def test_error_on_text_body(misc):
    with _client(misc, lambda r: httpx.Response(500, text="boom")) as client:
        with pytest.raises(misc.ApiError) as info:
            client.download()
    assert (info.value.status_code, info.value.body) == (500, "boom")
    assert info.value.url == "https://eu.example.com/api/blob"


def test_warnings_for_unsupported_parts():
    gen = Generator(SPEC, "Misc")
    gen.generate()
    assert any("cookie parameter 'session'" in w for w in gen.warnings)
    assert any("'cookieKey'" in w for w in gen.warnings)


def test_output_is_ascii_deterministic_and_tidy(misc):
    first = generate(SPEC, "Misc")
    assert first == generate(json.loads(json.dumps(SPEC)), "Misc")
    assert first.isascii()
    assert "\t" not in first and "\r" not in first
    assert all(line == line.rstrip() for line in first.splitlines())
    assert first.endswith("\n") and not first.endswith("\n\n")
    doc = misc.Misc.__doc__
    assert "Caf\u00e9 \"quotes\" and \\ back\\slash" in doc
    assert '"""triple quotes"""' in misc.Misc.search.__doc__


def test_load_yaml_and_json(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(SPEC, sort_keys=False), encoding="utf-8")
    assert generate(load_spec(tmp_path / "s.json"), "Misc") == generate(load_spec(tmp_path / "s.yaml"), "Misc")


@pytest.mark.parametrize("doc, message", [
    ({"swagger": "2.0"}, "Swagger 2.0"),
    ({"openapi": "2.5"}, "OpenAPI 3.x"),
    ([1, 2], "mapping"),
])
def test_rejects_non_openapi3(doc, message):
    with pytest.raises(SpecError, match=message):
        generate(doc)


def test_rejects_bad_class_name():
    with pytest.raises(SpecError, match="class-name"):
        generate(SPEC, "not valid")


def test_cli_stdout_check_and_errors(tmp_path, capsys):
    spec = tmp_path / "misc.yaml"
    spec.write_text(yaml.safe_dump(SPEC, sort_keys=False), encoding="utf-8")
    out = tmp_path / "client.py"

    assert main([str(spec)]) == 0
    printed = capsys.readouterr()
    assert "class MiscAPIClient:" in printed.out  # default name from info.title
    assert "warning: GET /search: cookie parameter" in printed.err

    assert main([str(spec), "-o", str(out), "--check"]) == 1
    assert main([str(spec), "-o", str(out)]) == 0
    assert main([str(spec), "-o", str(out), "--check"]) == 0
    assert b"\r\n" not in out.read_bytes()

    bad = tmp_path / "bad.yaml"
    bad.write_text("swagger: '2.0'\n", encoding="utf-8")
    assert main([str(bad)]) == 2
    assert main([str(tmp_path / "missing.yaml")]) == 2
    assert main([str(spec), "--class-name", "class"]) == 2
