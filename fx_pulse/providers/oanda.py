"""OANDA v20 streaming adapter."""
from __future__ import annotations

import os
from typing import Iterable, Iterator

from oandapyV20 import API
from oandapyV20.endpoints.pricing import PricingStream

from fx_pulse.providers.base import Tick


class OandaTickStream:
    def __init__(self, token: str, account_id: str, env: str = "practice") -> None:
        self._api = API(access_token=token, environment=env)
        self._account_id = account_id

    @classmethod
    def from_env(cls) -> "OandaTickStream":
        token = os.environ.get("OANDA_API_TOKEN")
        account_id = os.environ.get("OANDA_ACCOUNT_ID")
        if not token or not account_id:
            raise RuntimeError(
                "set OANDA_API_TOKEN and OANDA_ACCOUNT_ID "
                "(e.g. in a .env file, then run with `uv run --env-file .env ...`)"
            )
        return cls(token, account_id, os.getenv("OANDA_ENV", "practice"))

    def stream(self, instruments: Iterable[str]) -> Iterator[Tick]:
        params = {"instruments": ",".join(instruments)}
        request = PricingStream(accountID=self._account_id, params=params)
        for msg in self._api.request(request):
            if msg.get("type") != "PRICE":
                continue
            yield Tick(
                instrument=msg["instrument"],
                time=msg["time"],
                bid=float(msg["bids"][0]["price"]),
                ask=float(msg["asks"][0]["price"]),
            )
