"""Call every operation of the generated Petstore client against the local mock.

Run from the project folder:  python examples/demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from petstore_client import NewPet, Owner, PetStore, PetStoreError  # noqa: E402
from petstore_mock import TOKEN, serve  # noqa: E402

with serve() as (base_url, state), PetStore(base_url, token=TOKEN) as api:
    pets = api.list_pets(status="available", limit=10)
    print("list_pets ->", pets)
    print("  sent:", state.last["path"], state.last["query"], "auth:", state.last["headers"]["authorization"])

    kit = api.create_pet(NewPet(name="Kit", status="pending", owner=Owner(name="Lu")))
    print("create_pet ->", kit)
    print("  sent body:", state.last["body"])

    print("get_pet_by_id(3) ->", api.get_pet_by_id(3))
    print("update_pet(3) ->", api.update_pet(3, NewPet(name="Kitty", status="sold")))
    print("delete_pet(3) ->", api.delete_pet(3))
    try:
        api.get_pet_by_id(3)
    except PetStoreError as err:
        print("get_pet_by_id(3) ->", type(err).__name__, err.status_code, err.body)
