from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid
from app.db.session import get_db_session
from app.api.deps import get_current_user, RoleChecker
from app.schemas.customer import CustomerOut, CustomerCreate, CustomerUpdate, CustomerPaginated
from app.schemas.import_history import ImportHistoryOut
from app.repositories.customer import CustomerRepository
from app.repositories.import_history import ImportHistoryRepository
from app.services.import_service import CustomerImportService
from app.models.user import User

router = APIRouter()

@router.get("/", response_model=CustomerPaginated)
async def list_customers(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Retrieve customers list with paginated search, filter, and page limits."""
    customer_repo = CustomerRepository(db)
    customers, total = await customer_repo.get_filtered(
        skip=skip,
        limit=limit,
        search_query=search,
        is_active=is_active
    )
    return {
        "total": total,
        "items": customers,
        "skip": skip,
        "limit": limit
    }

@router.post("/", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
async def create_single_customer(
    customer_in: CustomerCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Manually insert a single new customer into the database."""
    customer_repo = CustomerRepository(db)
    
    # Check duplicate phone
    existing = await customer_repo.get_by_phone(customer_in.phone_number)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A customer with this phone number already exists."
        )
        
    db_obj = await customer_repo.create(customer_in.model_dump())
    await db.commit()
    return db_obj

@router.put("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: uuid.UUID,
    customer_in: CustomerUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Update details of an existing customer."""
    customer_repo = CustomerRepository(db)
    
    customer = await customer_repo.get(customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found."
        )
        
    if customer_in.phone_number:
        # Check phone conflict
        existing = await customer_repo.get_by_phone(customer_in.phone_number)
        if existing and existing.id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A customer with this phone number already exists."
            )
            
    # Apply updates
    update_data = customer_in.model_dump(exclude_unset=True)
    updated_obj = await customer_repo.update(customer, update_data)
    await db.commit()
    return updated_obj

@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(
    customer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Delete a customer record from the database."""
    customer_repo = CustomerRepository(db)
    customer = await customer_repo.get(customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found."
        )
        
    await customer_repo.remove(customer_id)
    await db.commit()

@router.post("/import", response_model=ImportHistoryOut)
async def import_customers_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Upload a CSV or Excel file to bulk-import customer contacts with validation auditing."""
    file_bytes = await file.read()
    import_service = CustomerImportService(db)
    
    # Process
    import_result = await import_service.import_customers(
        filename=file.filename,
        file_content=file_bytes,
        uploaded_by=current_user.id
    )
    return import_result

@router.get("/imports", response_model=List[ImportHistoryOut])
async def list_import_history(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Retrieve full bulk imports history logs."""
    history_repo = ImportHistoryRepository(db)
    return await history_repo.get_ordered_history(skip=skip, limit=limit)
