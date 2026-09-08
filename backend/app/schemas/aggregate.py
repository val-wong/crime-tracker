from pydantic import BaseModel


class GridCellResponse(BaseModel):
    lon: float
    lat: float
    count: int


class IncidentAggregateResponse(BaseModel):
    cells: list[GridCellResponse] = []
    grid_degrees: float
    # "rollup" or "raw" -- which query path served this response (see
    # docs/rollup-design.md). Operational transparency for QA/tests,
    # not a user-facing feature; the frontend doesn't need to branch on
    # it, but tests and operators can confirm the fast path is actually
    # being taken.
    source: str = "raw"
