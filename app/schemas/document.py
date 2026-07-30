import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    filename: str
    file_type: str
    status: str
    total_chunks: int
    created_at: datetime
    updated_at: datetime

class SearchResultMatch(BaseModel):
    text: str
    score: float
    document_id: uuid.UUID
    filename: str
    chunk_index: int

class SearchResultOut(BaseModel):
    query: str
    results: List[SearchResultMatch]
