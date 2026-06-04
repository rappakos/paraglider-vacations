"""FastAPI entry point for the vacation recommender web app."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routes import setup_routes


def create_app() -> FastAPI:
    app = FastAPI(
        title="Paraglider Vacations",
        description="Seasonal region recommendations from historical DHV-XC flight data.",
        version="0.1.0",
    )

    init_db()  # CREATE TABLE IF NOT EXISTS — idempotent

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    setup_routes(app)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from app.config import PORT

    uvicorn.run("app.main:app", host="localhost", port=PORT, reload=True)
