from typing import Optional
import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwk, jwt, JWTError
from app.core.config import settings
from app.core.database import get_db

bearer_scheme = HTTPBearer()

_jwks_cache: Optional[dict] = None


def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        resp = httpx.get(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json", timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
    return _jwks_cache


def decode_token(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")

        if alg == "HS256":
            key = settings.supabase_jwt_secret
        else:
            jwks = _get_jwks()
            matching = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
            if matching is None:
                # JWKS may have rotated since caching — refresh once and retry
                global _jwks_cache
                _jwks_cache = None
                jwks = _get_jwks()
                matching = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
            if matching is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown signing key")
            key = jwk.construct(matching, alg)

        payload = jwt.decode(
            token,
            key,
            algorithms=[alg],
            options={"verify_aud": False},
        )
        return payload
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    full_name = (payload.get("user_metadata") or {}).get("full_name")
    return {
        "id": user_id,
        "email": payload.get("email"),
        "full_name": full_name,
        "token": credentials.credentials,
    }


async def get_current_user_roles(user: dict = Depends(get_current_user)) -> dict:
    db = get_db()
    resp = (
        db.table("user_roles")
        .select("roles(name)")
        .eq("user_id", user["id"])
        .execute()
    )
    roles = [r["roles"]["name"] for r in resp.data if r.get("roles")]
    return {**user, "roles": roles}


def require_roles(*allowed: str):
    async def checker(user: dict = Depends(get_current_user_roles)) -> dict:
        if not any(r in user["roles"] for r in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required roles: {list(allowed)}",
            )
        return user
    return checker
