from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db_session
from app.api.deps import get_current_user, RoleChecker
from app.schemas.user import UserOut, UserCreate
from app.repositories.user import UserRepository
from app.services.security import hash_password
from app.models.user import User

router = APIRouter()

@router.get("/me", response_model=UserOut)
async def get_my_profile(current_user: User = Depends(get_current_user)):
    """Fetch profile data of the currently logged-in user."""
    return current_user

@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin"]))
):
    """Create a new user. Restricted to system administrators."""
    user_repo = UserRepository(db)
    
    # Check if email exists
    existing = await user_repo.get_by_email(user_in.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address already exists."
        )
        
    hashed = hash_password(user_in.password)
    user_obj = User(
        email=user_in.email,
        hashed_password=hashed,
        role=user_in.role,
        is_active=True
    )
    
    await user_repo.create(user_obj)
    await db.commit()
    return user_obj
