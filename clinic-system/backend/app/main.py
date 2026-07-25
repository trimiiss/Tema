from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import patients, appointments, documents, reports, agents, users

app = FastAPI(
    title="Clinic Multi-Agent System",
    description="Administrative multi-agent prototype for diploma thesis",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://frontend:3000"],
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


@app.get("/health")
def health():
    return {"status": "ok"}
