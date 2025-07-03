from pydantic import BaseModel, Field
from typing import Literal

class User(BaseModel):
    id: str = Field(..., description="Unique user key/id")
    name: str
    company: str
    status: Literal['active', 'inactive'] = 'active'
