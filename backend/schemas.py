from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class VmMetadataCreate(BaseModel):
    vm_id:        str
    host_name:    str
    description:  Optional[str]      = ""
    owner:        Optional[str]      = ""
    borrow_until: Optional[datetime] = None
    notes:        Optional[str]      = ""


class VmMetadataUpdate(BaseModel):
    description:  Optional[str]      = None
    owner:        Optional[str]      = None
    borrow_until: Optional[datetime] = None
    notes:        Optional[str]      = None


class VmMetadataResponse(BaseModel):
    vm_id:        str
    host_name:    str
    description:  Optional[str]      = ""
    owner:        Optional[str]      = ""
    borrow_until: Optional[datetime] = None
    notes:        Optional[str]      = ""
    tags:         Optional[List[str]] = []
    vm_username:  Optional[str]      = ""
    vm_password:  Optional[str]      = ""
    updated_at:   Optional[datetime] = None
