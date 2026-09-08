from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.incidents import router as incidents_router
from app.api.status import router as status_router
from app.api.summary import router as summary_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Crime Tracker API",
    description=(
        "Serves normalized, aggregate public crime-incident data. "
        "Does not predict individual behavior; see docs/product.md."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # The frontend only ever issues plain, unauthenticated `fetch()` GET
    # requests (see frontend/src/api/client.ts) -- no cookies or
    # Authorization headers are sent, so credentialed CORS isn't needed.
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(incidents_router)
app.include_router(status_router)
app.include_router(summary_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
