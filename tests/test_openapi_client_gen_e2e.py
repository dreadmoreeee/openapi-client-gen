"""Generate the bundled Petstore client and call every operation on a local mock."""

from __future__ import annotations

import dataclasses
import inspect
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from openapi_client_gen.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
SPEC = EXAMPLES / "petstore.yaml"


@pytest.fixture
def mod(tmp_path, load_generated):
    out = tmp_path / "petstore_client.py"
    assert main([str(SPEC), "-o", str(out), "--class-name", "PetStore"]) == 0
    py_compile.compile(str(out), cfile=str(tmp_path / "petstore_client.pyc"), doraise=True)
    return load_generated(out.read_text(encoding="ascii"), "petstore")


@pytest.fixture
def api(mod, petstore_server):
    base_url, state = petstore_server
    with mod.PetStore(base_url, token=state.TOKEN) as client:
        yield client


def test_generated_module_shape(mod):
    assert dataclasses.is_dataclass(mod.Pet)
    assert mod.ApiError is mod.PetStoreError
    assert set(mod.__all__) >= {"PetStore", "PetStoreError", "Pet", "NewPet", "Owner", "PetStatus", "Error"}
    sig = inspect.signature(mod.PetStore.list_pets)
    assert [p.kind for p in list(sig.parameters.values())[1:]] == [inspect.Parameter.KEYWORD_ONLY] * 3
    assert sig.parameters["limit"].default is None
    sig = inspect.signature(mod.PetStore.update_pet)
    assert list(sig.parameters)[1:] == ["pet_id", "body"]
    assert mod.PetStore.get_pet_by_id.__doc__.startswith("Get a pet by id.")


def test_list_pets_sends_bearer_query_and_header(api, petstore_server, mod):
    _, state = petstore_server
    pets = api.list_pets(status="available", limit=5, x_request_id="req-42")
    assert state.last["headers"]["authorization"] == "Bearer secret-token"
    assert state.last["headers"]["x-request-id"] == "req-42"
    assert state.last["query"] == {"status": ["available"], "limit": ["5"]}
    assert state.last["path"] == "/v1/pets"
    assert [type(p) for p in pets] == [mod.Pet]
    rex = pets[0]
    assert rex.name == "Rex" and rex.id == 1 and rex.tags == ["dog"]
    assert isinstance(rex.owner, mod.Owner) and rex.owner.email == "ana@example.com"
    assert rex.previous_owners == [mod.Owner(name="Bo")]


def test_optional_params_are_left_out(api, petstore_server):
    _, state = petstore_server
    pets = api.list_pets()
    assert state.last["query"] == {}
    assert "x-request-id" not in state.last["headers"]
    assert [p.name for p in pets] == ["Rex", "Tom"]
    assert pets[1].owner is None and pets[1].birth_date is None


def test_api_key_auth(mod, petstore_server):
    base_url, state = petstore_server
    with mod.PetStore(base_url, api_key=state.API_KEY) as client:
        assert len(client.list_pets(limit=1)) == 1
    assert state.last["headers"]["x-api-key"] == "secret-key"
    assert "authorization" not in state.last["headers"]


def test_missing_credentials_raise_typed_error(mod, petstore_server):
    base_url, _ = petstore_server
    with mod.PetStore(base_url) as client, pytest.raises(mod.PetStoreError) as info:
        client.list_pets()
    assert info.value.status_code == 401
    assert info.value.body == {"code": 401, "message": "unauthorized"}


def test_create_pet_sends_json_body(api, petstore_server, mod):
    _, state = petstore_server
    new = mod.NewPet(name="Kit", status="pending", tags=["cat"], owner=mod.Owner(name="Lu", phones=["555"]))
    pet = api.create_pet(new)
    assert state.last["method"] == "POST"
    assert state.last["headers"]["content-type"] == "application/json"
    assert state.last["body"] == {"name": "Kit", "status": "pending", "tags": ["cat"],
                                  "owner": {"name": "Lu", "phones": ["555"]}}
    assert isinstance(pet, mod.Pet)
    assert (pet.id, pet.name, pet.status) == (3, "Kit", "pending")
    assert pet.owner == mod.Owner(name="Lu", phones=["555"])


def test_create_pet_validation_error(api, mod):
    with pytest.raises(mod.ApiError) as info:
        api.create_pet(mod.NewPet(name=""))
    assert info.value.status_code == 422
    assert info.value.body["message"] == "name is required"


def test_get_pet_and_404(api, petstore_server, mod):
    base_url, state = petstore_server
    pet = api.get_pet_by_id(1)
    assert isinstance(pet, mod.Pet) and pet.birth_date == "2021-04-01"
    assert state.last["path"] == "/v1/pets/1"
    with pytest.raises(mod.PetStoreError) as info:
        api.get_pet_by_id(999)
    err = info.value
    assert (err.status_code, err.method, err.url) == (404, "GET", f"{base_url}/pets/999")
    assert err.body == {"code": 404, "message": "pet 999 not found"}
    assert isinstance(err.response, httpx.Response)
    assert "returned HTTP 404" in str(err)


def test_update_pet(api, petstore_server, mod):
    _, state = petstore_server
    pet = api.update_pet(2, mod.NewPet(name="Tommy", status="available"))
    assert state.last["method"] == "PUT" and state.last["path"] == "/v1/pets/2"
    assert state.last["body"] == {"name": "Tommy", "status": "available"}
    assert (pet.id, pet.name, pet.status) == (2, "Tommy", "available")
    with pytest.raises(mod.PetStoreError):
        api.update_pet(999, mod.NewPet(name="Ghost"))


def test_delete_pet(api, petstore_server, mod):
    _, state = petstore_server
    assert api.delete_pet(1) is None
    assert state.last["method"] == "DELETE"
    with pytest.raises(mod.PetStoreError) as info:
        api.delete_pet(1)
    assert info.value.status_code == 404


def test_round_trip_to_dict(mod):
    raw = {"name": "Rex", "status": "sold", "id": 7, "owner": {"name": "Ana"},
           "previousOwners": [{"name": "Bo", "email": "bo@example.com"}]}
    assert mod.Pet.from_dict(raw).to_dict() == raw


def test_context_manager_and_injected_client(mod):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"name": "Odd", "status": "sold", "id": 5})

    with mod.PetStore("http://api.test/v1/", transport=httpx.MockTransport(handler)) as client:
        pet = client.get_pet_by_id("a/b c")
    assert client._client.is_closed
    assert seen[0].url.raw_path == b"/v1/pets/a%2Fb%20c"
    assert pet.id == 5

    injected = httpx.Client(transport=httpx.MockTransport(handler))
    with mod.PetStore("http://api.test/v1", client=injected, headers={"X-Trace": "1"}) as client:
        client.get_pet_by_id(5)
    assert not injected.is_closed
    assert seen[1].headers["x-trace"] == "1"
    injected.close()
    with pytest.raises(ValueError):
        mod.PetStore(client=injected, transport=httpx.MockTransport(handler))


def test_committed_example_is_up_to_date():
    out = EXAMPLES / "petstore_client.py"
    assert main([str(SPEC), "-o", str(out), "--class-name", "PetStore", "--check"]) == 0


def test_demo_script_runs_against_mock():
    result = subprocess.run([sys.executable, str(EXAMPLES / "demo.py")], capture_output=True,
                            text=True, timeout=60, env={**os.environ, "PYTHONUTF8": "1"})
    assert result.returncode == 0, result.stderr
    assert "get_pet_by_id(3) -> PetStoreError 404" in result.stdout
