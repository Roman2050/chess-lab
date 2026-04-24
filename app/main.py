from fastapi import FastAPI
from app.routers import games


app = FastAPI(
    title="Chess Lab API",
    description="Chess game analysis and training system",
    version="0.1.0"
)

app.include_router(games.router)

@app.get("/health")
def health_check():
    return {"status": "ok"} 