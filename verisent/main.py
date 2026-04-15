from fastapi import FastAPI
from verisend.api import routes
from fastapi.middleware.cors import CORSMiddleware
import logging
import logfire


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# logfire.configure()

app = FastAPI(
    title="Verisend API",
)

# logfire.instrument_fastapi(app)
# logfire.instrument_pydantic_ai()
# logfire.instrument_openai()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://verisend.zylentra.org"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

