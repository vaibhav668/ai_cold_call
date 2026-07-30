import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict

class ImportHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: str
    total_records: int
    successfully_imported: int
    failed_records: int
    error_details: List[Dict[str, Any]]
    uploaded_by: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
