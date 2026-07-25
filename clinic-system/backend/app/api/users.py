from fastapi import APIRouter, Depends
from app.core.auth import get_current_user_roles

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user_roles)):
    return {
        "id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "roles": user["roles"],
    }
