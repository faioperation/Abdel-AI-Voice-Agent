from fastapi import APIRouter
from pydantic import BaseModel
import os
from typing import Optional

router = APIRouter(prefix="/api/verify-address", tags=["Address Verification"])

class AddressVerificationRequest(BaseModel):
    postal_code: str
    address: str

class AddressVerificationResponse(BaseModel):
    deliverable: bool
    suggestion: Optional[str] = None

@router.post("/", response_model=AddressVerificationResponse)
async def verify_address(data: AddressVerificationRequest):
    postal_code = data.postal_code.strip()
    address = data.address.strip().lower()
    
    # Path to the postal code file
    file_path = os.path.join("Address_data", f"{postal_code}.txt")
    
    if not os.path.exists(file_path):
        return AddressVerificationResponse(
            deliverable=False, 
            suggestion="Delivery is not available for this postal code. Please offer pickup."
        )
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # 1. Exact substring match
        for line in lines:
            if address in line.lower():
                return AddressVerificationResponse(deliverable=True)
                
        # 2. First word match (loose matching for street name)
        address_words = address.split()
        if address_words:
            first_word = address_words[0]
            # Only match if the first word is substantial
            if len(first_word) >= 3:
                for line in lines:
                    if first_word in line.lower():
                        return AddressVerificationResponse(deliverable=True)

        return AddressVerificationResponse(
            deliverable=False,
            suggestion="Street not found in the delivery zone. Please offer pickup."
        )
        
    except Exception as e:
        print(f"Error reading address file: {e}")
        return AddressVerificationResponse(
            deliverable=False,
            suggestion="Could not verify address due to server error. Please offer pickup."
        )
