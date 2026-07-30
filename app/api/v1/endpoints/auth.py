from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db_session
from app.schemas.token import Token, RefreshPayload
from app.services.auth import AuthService

router = APIRouter()

@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db_session)
):
    """Authenticate email and password using form parameters. Required for OAuth2 Swagger UI flow."""
    auth_service = AuthService(db)
    access_token, refresh_token = await auth_service.authenticate(
        email=form_data.username,
        password=form_data.password
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/refresh", response_model=Token)
async def refresh_token(
    payload: RefreshPayload,
    db: AsyncSession = Depends(get_db_session)
):
    """Rotate access and refresh tokens using a valid refresh token."""
    auth_service = AuthService(db)
    access_token, refresh_token = await auth_service.refresh_session(payload.refresh_token)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshPayload,
    db: AsyncSession = Depends(get_db_session)
):
    """Revoke a refresh token, invalidating the session."""
    auth_service = AuthService(db)
    await auth_service.revoke_session(payload.refresh_token)
