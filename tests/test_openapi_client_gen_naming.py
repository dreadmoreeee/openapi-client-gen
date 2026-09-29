"""Identifier naming: snake/Pascal case, keywords, fallbacks and collisions."""

from __future__ import annotations

import inspect
from typing import Literal

import pytest

from openapi_client_gen import generate
from openapi_client_gen.naming import dedupe, is_valid_class_name, operation_name, pascal_case, snake_case


@pytest.mark.parametrize("raw, expected", [
    ("listPets", "list_pets"),
    ("getPetByID", "get_pet_by_id"),
    ("HTTPServerStatus", "http_server_status"),
    ("X-Request-Id", "x_request_id"),
    ("pet.name", "pet_name"),
    ("already_snake", "already_snake"),
    ("class", "class_"),
    ("from", "from_"),
    ("None", "none"),
    ("2fa_code", "x_2fa_code"),
    ("caf\u00e9", "cafe"),
    ("$$$", "value"),
    ("type", "type"),
])
def test_snake_case(raw, expected):
    assert snake_case(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("pet", "Pet"),
    ("pet_status", "PetStatus"),
    ("Pet-Status", "PetStatus"),
    ("HTTPError", "HTTPError"),
    ("3dModel", "Model3dModel"),
    ("", "Model"),
])
def test_pascal_case(raw, expected):
    assert pascal_case(raw) == expected


@pytest.mark.parametrize("method, path, op_id, expected", [
    ("get", "/pets/{petId}", None, "get_pets_by_pet_id"),
    ("post", "/pets", None, "post_pets"),
    ("get", "/users/{userId}/pets/{petId}", None, "get_users_by_user_id_pets_by_pet_id"),
    ("get", "/", None, "get"),
    ("delete", "/v1/items/{id}", "removeItem", "remove_item"),
    ("get", "/x", "import", "import_"),
])
def test_operation_name(method, path, op_id, expected):
    assert operation_name(method, path, op_id) == expected


def test_dedupe():
    taken = {"body"}
    assert dedupe("body", taken, hint="query") == "body_query"
    assert dedupe("body", taken, hint="query") == "body_2"
    assert dedupe("pet", taken) == "pet"
    assert dedupe("Pet", {"Pet"}, hint="Model", sep="") == "PetModel"


def test_is_valid_class_name():
    assert is_valid_class_name("PetStore")
    assert not is_valid_class_name("class")
    assert not is_valid_class_name("Pet Store")
    assert not is_valid_class_name("Caf\u00e9")


def test_collisions_in_generated_code(load_generated):
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Clash", "version": "1"},
        "paths": {
            "/a/{from}": {
                "get": {
                    "operationId": "fetch",
                    "parameters": [
                        {"name": "from", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "body", "in": "query", "schema": {"type": "string"}},
                        {"name": "headers", "in": "query", "schema": {"type": "string"}},
                        {"name": "from", "in": "query", "schema": {"type": "string"}},
                    ],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"204": {"description": "ok"}},
                },
                "post": {"operationId": "fetch", "responses": {"204": {"description": "ok"}}},
            },
            "/close": {"get": {"responses": {"204": {"description": "ok"}}}},
            "/b": {"get": {"operationId": "close", "responses": {"204": {"description": "ok"}}}},
        },
        "components": {"schemas": {
            "clash": {"type": "string", "enum": ["a"]},
            "Clash": {"type": "object", "properties": {
                "class": {"type": "string"}, "to_dict": {"type": "string"},
                "fooBar": {"type": "string"}, "foo_bar": {"type": "string"}}},
            "ClashError": {"type": "object", "properties": {"x": {"type": "integer"}}},
        }},
    }
    mod = load_generated(generate(spec, "Clash"))
    assert {"fetch", "fetch_2", "get_close", "close_2"} <= set(vars(mod.Clash))
    params = list(inspect.signature(mod.Clash.fetch).parameters)
    assert params == ["self", "from_", "body", "body_query", "headers_query", "from_query"]
    # 'Clash' is taken by the client class and 'ClashError' by its error class.
    assert mod.ClashModel == Literal["a"]
    assert mod.ClashErrorModel.__name__ == "ClashErrorModel"
    fields = [f for f in mod.Clash2.__dataclass_fields__]
    assert fields == ["class_", "to_dict_2", "foo_bar", "foo_bar_2"]
    obj = mod.Clash2.from_dict({"class": "c", "to_dict": "t", "fooBar": "1", "foo_bar": "2"})
    assert obj.to_dict() == {"class": "c", "to_dict": "t", "fooBar": "1", "foo_bar": "2"}
