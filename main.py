from fastapi import FastAPI
from contextlib import asynccontextmanager
import os

# Import the router but don't initialize the agent yet
from api.routes import api_router_factory


app = FastAPI(title="Bill Splitter API", version="1.0.0")

# Include API routes after app creation
app.include_router(api_router_factory())

@app.get("/")
async def root():
    return {"message": "Bill Splitter API is running!"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    # Get port from environment variable (Render provides PORT)
    port = int(os.environ.get("PORT", 8000))
    # Bind to 0.0.0.0 instead of localhost for external access
    uvicorn.run(app, host="0.0.0.0", port=port)