from fastapi import FastAPI


app = FastAPI(
    title="Chess Lab API",
    description="Chess game analysis and training system",
    version="0.1.0"
)

@app.get("/health")
def health_check():
    return {"status": "ok"} 