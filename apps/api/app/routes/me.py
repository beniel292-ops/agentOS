from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth import Current, get_current

router = APIRouter(prefix="/v1")


@router.get("/me")
def me(current: Annotated[Current, Depends(get_current)]) -> dict:
    return {
        "tenant": {"id": current.tenant.id, "name": current.tenant.name},
        "user": {"id": current.user.id, "role": current.user.role},
    }
