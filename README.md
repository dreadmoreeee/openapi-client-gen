# openapi-client-gen

Turn an OpenAPI 3.x spec (YAML or JSON) into **one small, readable, typed Python module**: dataclasses for the schemas and an `httpx` client with one method per operation.

```
python -m openapi_client_gen spec.yaml -o client.py --class-name PetStore
```

```python
from client import NewPet, PetStore, PetStoreError

with PetStore(token="...") as api:
    pets = api.list_pets(status="available", limit=10)   # -> list[Pet]
    kit = api.create_pet(NewPet(name="Kit"))             # -> Pet
    try:
        api.get_pet_by_id(999)
    except PetStoreError as err:
        print(err.status_code, err.body)
```

Why another generator:

- **Output you can read and commit.** One module, no runtime package from this tool, no templates to learn. The only dependency of the generated code is `httpx`. The generator itself needs only PyYAML.
- **Typed.** Component schemas become `@dataclass` classes with `from_dict` / `to_dict` (nested objects, arrays, maps, optional fields). Enums become `Literal[...]`. The return type is the dataclass (or `list[...]`) when the 2xx response is a known schema.
- **Natural signatures.** Path params are positional, query and header params are keyword-only (`None` = not sent), and a JSON body is a typed argument.
- **Auth from `securitySchemes`.** Bearer token (also used for oauth2/OpenID Connect access tokens), HTTP basic, and API keys in a header or the query string become constructor arguments.
- **Testable.** Inject an `httpx.Client` or a transport (for example `httpx.MockTransport`). Supports `with` and `close()`.
- **Typed errors.** Any non-2xx response raises `{ClassName}Error` (also exported as `ApiError`), which carries `status_code`, `body` (JSON or text), `method`, `url` and `response`.
- **Deterministic.** The output is ASCII-only with 4-space indents and no trailing whitespace, and it follows the spec's order. It is checked with `compile()` before it is written. `--check` makes CI fail when a committed client is out of date.

## Install and use

```
pip install .                      # from this folder; needs Python 3.10+
pip install httpx                  # needed by the generated client
python -m openapi_client_gen examples/petstore.yaml -o petstore_client.py --class-name PetStore
python -m openapi_client_gen examples/petstore.yaml -o petstore_client.py --class-name PetStore --check
```

Without `-o`, the source is printed to stdout. Without `--class-name`, the name comes from `info.title` (`Petstore` gives `PetstoreClient`). Anything the generator skips is printed to stderr as a `warning:` line.

From Python: `generate(load_spec("spec.yaml"), "PetStore")` returns the source as a string.

## Measured result

The bundled [examples/petstore.yaml](examples/petstore.yaml) has list/create/get/update/delete, a status filter and a limit, bearer and API-key auth, a nested `Owner`, an `allOf`-composed `Pet` and a `PetStatus` enum. The generated [examples/petstore_client.py](examples/petstore_client.py) is committed.

```
$ python -m openapi_client_gen examples/petstore.yaml -o examples/petstore_client.py --class-name PetStore
wrote examples\petstore_client.py (375 lines, class PetStore)
```

Excerpt of the generated code:

```python
# Where the pet is in the adoption process.
PetStatus = Literal["available", "pending", "sold"]

# ...

@dataclass
class Pet:
    """A pet in the store.

    Attributes:
        name: The pet's name.
    """

    name: str
    status: PetStatus
    id: int
    tags: list[str] | None = None
    owner: Owner | None = None
    birth_date: str | None = None
    previous_owners: list[Owner] | None = None

    # ... from_dict() / to_dict(), then in class PetStore:

    def list_pets(
        self,
        *,
        status: PetStatus | None = None,
        limit: int | None = None,
        x_request_id: str | None = None,
    ) -> list[Pet]:
        """List pets.

        ``GET /pets``

        Returns the pets in the store, optionally filtered by status.

        Args:
            status: Only return pets with this status.
            limit: Maximum number of pets to return.
            x_request_id: Optional id echoed in server logs.
        """
        params = {"status": status, "limit": limit}
        headers = {"X-Request-Id": x_request_id}
        response = self._request("GET", "/pets", params=params, headers=headers)
        return [Pet.from_dict(v0) for v0 in response.json()]
```

Real calls from [examples/demo.py](examples/demo.py) against the local mock server ([examples/petstore_mock.py](examples/petstore_mock.py), standard library, random free port):

```
$ python examples/demo.py
list_pets -> [Pet(name='Rex', status='available', id=1, tags=['dog'], owner=Owner(name='Ana', email='ana@example.com', phones=None), birth_date='2021-04-01', previous_owners=[Owner(name='Bo', email=None, phones=None)])]
  sent: /v1/pets {'status': ['available'], 'limit': ['10']} auth: Bearer secret-token
create_pet -> Pet(name='Kit', status='pending', id=3, tags=None, owner=Owner(name='Lu', email=None, phones=None), birth_date=None, previous_owners=None)
  sent body: {'name': 'Kit', 'status': 'pending', 'owner': {'name': 'Lu'}}
get_pet_by_id(3) -> Pet(name='Kit', status='pending', id=3, tags=None, owner=Owner(name='Lu', email=None, phones=None), birth_date=None, previous_owners=None)
update_pet(3) -> Pet(name='Kitty', status='sold', id=3, tags=None, owner=None, birth_date=None, previous_owners=None)
delete_pet(3) -> None
get_pet_by_id(3) -> PetStoreError 404 {'code': 404, 'message': 'pet 3 not found'}

$ python -m pytest -q -p no:cacheprovider --import-mode=importlib openapi-client-gen
........................................................................ [ 67%]
..................................                                       [100%]
106 passed in 5.05s
```

The tests work offline. They generate the client into a temp folder, `py_compile` it, import it and call every operation against the mock. They check auth headers, query params, JSON bodies, dataclass return types and the typed 404 error. The suite also has an exact snapshot test (`tests/snapshots/tiny_client.py`, which you can regenerate with `UPDATE_SNAPSHOTS=1`) and unit tests for naming, `$ref` resolution and type mapping.

## Type mapping

| OpenAPI | Python |
|---|---|
| `string` (any format, including date/date-time) | `str` |
| `integer` / `number` / `boolean` | `int` / `float` / `bool` |
| `array` of X | `list[X]` |
| object schema in `components` | `@dataclass` with `from_dict` / `to_dict` |
| inline object, or `additionalProperties: X` | `dict[str, Any]` / `dict[str, X]` |
| `enum` of strings, ints or bools | `Literal[...]` |
| `nullable: true`, or `type: [T, "null"]` (3.1) | `T \| None` |
| `allOf` | properties merged into one dataclass |
| `oneOf` / `anyOf`, or no schema | `Any` |

Names are snake_case from `operationId`. Without one, the name comes from the method and the path (`GET /pets/{petId}` gives `get_pets_by_pet_id`). Python keywords get a trailing `_` (`from_`, `class_`). Name clashes get a suffix (`fetch_2`, or `body_query` for a query param called `body`).

## Limitations

This is deliberately small. It does **not** support:

- `$ref` to other files or URLs (only local `#/...` pointers), or Swagger 2.0.
- `oneOf`/`anyOf`/`discriminator` polymorphism (typed as `Any`, returned as plain JSON), and inline nested objects (typed `dict[str, Any]`, not their own class).
- Formats: date/date-time stay strings, `binary` is not converted, and `readOnly`/`writeOnly` are ignored. `from_dict` drops unknown keys and raises `KeyError` when a required key is missing.
- Cookie parameters and cookie API keys (skipped with a warning), non-default `style`/`explode` serialization, and `deepObject` query params.
- Multipart bodies only as a raw pass-through to httpx `files=`. Form bodies are sent as a plain dict.
- Per-operation `security`: the credentials you pass are sent with every request. OAuth2 flows are not run. Pass an access token as `token`.
- Several 2xx responses or several media types: the lowest 2xx code and the first JSON media type are used. There is no async client, retries or pagination.
- Server variables: only their default values are used for the default `base_url`.

## Author

Marvin Palencia, founder of [DeMark Studio](https://demarkstudio.ca), Miramichi, New Brunswick, Canada. Portfolio: [marvin.demarkstudio.ca](https://marvin.demarkstudio.ca)

MIT License.
