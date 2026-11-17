from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    exchange: str
    segment: str
    name: str
    isin: str | None
    instrument_type: str
    lot_size: int
    tick_size: float
    expiry: date | None
    strike: float | None
    option_type: str | None
    sector: str | None
    industry: str | None
    is_index: bool
    is_simulated: bool


def instrument_dict(inst) -> dict:  # noqa: ANN001
    return InstrumentOut.model_validate(inst).model_dump(mode="json")
