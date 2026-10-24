"""The models a gateway offers the signed-in user, from its OpenAI-compatible model list."""

from .adapters import Api, Gateway
from .errors import CredentialError
from .transport import JsonTransport


def list_models(gateway: Gateway, token: str, *, transport: JsonTransport | None = None) -> list[str]:
    """Model ids (the aliases tools pass as their model) in the gateway's order; skips ids a terminal cannot show."""
    listed = (transport or JsonTransport()).request(
        "GET", gateway.endpoint(Api.OPENAI_CHAT) + "/models", headers={"Authorization": "Bearer " + token},
    )
    rows = listed.get("data")
    if not isinstance(rows, list):
        raise CredentialError("Gateway returned an unexpected model list.")
    models: list[str] = []
    for row in rows:
        model = row.get("id") if isinstance(row, dict) else None
        if isinstance(model, str) and model and model.isprintable() and model not in models:
            models.append(model)
    return models
