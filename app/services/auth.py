from datetime import datetime, timezone
from typing import Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.user import UserRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.models.refresh_token import RefreshToken
from app.core.exceptions import AppException
from app.services.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
)

class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)
        self.token_repo = RefreshTokenRepository(db)

    async def authenticate(self, email: str, password: str) -> Tuple[str, str]:
        """Authenticate user credentials and return access + refresh tokens."""
        user = await self.user_repo.get_by_email(email)
        if not user or not user.is_active:
            raise AppException("Invalid email or password", 401)
            
        if not verify_password(password, user.hashed_password):
            raise AppException("Invalid email or password", 401)
            
        # Create access token
        access_token = create_access_token(str(user.id), user.role)
        
        # Create refresh token
        token_str, expires_at = create_refresh_token(str(user.id))
        
        # Save refresh token in database
        refresh_token_obj = RefreshToken(
            token=token_str,
            user_id=user.id,
            expires_at=expires_at
        )
        await self.token_repo.create(refresh_token_obj)
        await self.db.commit()
        
        return access_token, token_str

    async def refresh_session(self, refresh_token_str: str) -> Tuple[str, str]:
        """Rotate the refresh token and return a new access + refresh token pair."""
        token_record = await self.token_repo.get_by_token(refresh_token_str)
        if not token_record or token_record.is_revoked:
            raise AppException("Invalid or revoked refresh token", 401)
            
        # Check expiry
        expires_at_utc = token_record.expires_at.replace(tzinfo=timezone.utc)
        if expires_at_utc < datetime.now(timezone.utc):
            raise AppException("Expired refresh token", 401)
            
        user = await self.user_repo.get(token_record.user_id)
        if not user or not user.is_active:
            raise AppException("User inactive or not found", 401)
            
        # Generate new tokens
        new_access_token = create_access_token(str(user.id), user.role)
        new_token_str, new_expires_at = create_refresh_token(str(user.id))
        
        # Revoke old token
        await self.token_repo.update(token_record, {"is_revoked": True})
        
        # Create new refresh token
        new_refresh_token_obj = RefreshToken(
            token=new_token_str,
            user_id=user.id,
            expires_at=new_expires_at
        )
        await self.token_repo.create(new_refresh_token_obj)
        await self.db.commit()
        
        return new_access_token, new_token_str

    async def revoke_session(self, refresh_token_str: str) -> None:
        """Revoke a refresh token to perform a user logout."""
        token_record = await self.token_repo.get_by_token(refresh_token_str)
        if token_record:
            await self.token_repo.update(token_record, {"is_revoked": True})
            await self.db.commit()
