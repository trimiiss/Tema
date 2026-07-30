from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import patients, appointments, documents, reports, agents, users, staff, public
from app.core import tasks
from app.core.config import cors_origins


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sync endpoints run in a threadpool with no event loop of their own, so
    # `tasks.spawn` needs a handle on this one to schedule agent runs onto.
    tasks.capture_loop()
    yield
    # Agent runs and document processing are fired off in the background; give
    # them a moment to finish so the process can exit cleanly. Without this the
    # worker never shuts down while a run is in flight, which is what made
    # `uvicorn --reload` hang and keep serving stale code.
    await tasks.drain()


app = FastAPI(
    title="Clinic Multi-Agent System",
    description="Administrative multi-agent prototype for diploma thesis",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(patients.router)
app.include_router(appointments.router)
app.include_router(documents.router)
app.include_router(reports.router)
app.include_router(agents.router)
app.include_router(users.router)
app.include_router(staff.router)
app.include_router(staff.services_router)
app.include_router(public.router)


@app.get("/health")
def health():
    return {"status": "ok"}
