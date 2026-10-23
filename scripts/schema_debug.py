import sys
import traceback

sys.path.insert(0, "scripts")

from gltest_cli.config.user import load_user_config, user_config_exists
from gltest_cli.config.general import get_general_config

if not user_config_exists():
    raise SystemExit("no gltest.config.yaml")
get_general_config().user_config = load_user_config("gltest.config.yaml")

from gltest.clients import (
    get_gl_client,
    get_gl_hosted_studio_client,
    get_local_client,
)

code = open("contracts/demo_marketplace.py", "rb").read()

for label, factory in (
    ("default", get_gl_client),
    ("hosted studio", get_gl_hosted_studio_client),
    ("local", get_local_client),
):
    print(f"--- {label}")
    try:
        client = factory()
        print("    endpoint:", getattr(client.provider, "endpoint_url", "?"))
        print("    chain id:", getattr(client.chain, "id", "?"))
        res = client.get_contract_schema_for_code(contract_code=code)
        print("    OK:", str(res)[:200])
    except Exception:
        traceback.print_exc()
