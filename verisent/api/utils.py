from fastapi import APIRouter

TAGS = [
    {
        "name": "Utils",
        "description": "Utility Routes",
    },
]

router = APIRouter(tags=["Utils"])

@router.get("/")
def read_root():
    return {"Hello": "World"}

@router.get("/hc")
def health_check():
    return ""