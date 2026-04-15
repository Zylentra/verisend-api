import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel


TAGS = [
    {
        "name": "Standard Fields",
        "description": "Catalogue of reusable standard fields",
    },
]

router = APIRouter(tags=["standard-fields"])


class StandardFieldResponse(BaseModel):
    key: str
    label: str
    field_type: str
    group: str | None = None


def _load_standard_fields() -> list[StandardFieldResponse]:
    path = Path(__file__).parent.parent.parent / "data" / "standard_fields.json"
    raw = json.loads(path.read_text())
    return [
        StandardFieldResponse(
            key=f["key"],
            label=f["label"],
            field_type=f["field_type"],
            group=f.get("group"),
        )
        for f in raw
    ]


STANDARD_FIELDS = _load_standard_fields()


@router.get("/standard-fields", response_model=list[StandardFieldResponse])
async def get_standard_fields():
    return STANDARD_FIELDS
