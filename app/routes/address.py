import os
import difflib
import re
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/verify-address", tags=["Address Verification"])

class AddressVerificationRequest(BaseModel):
    postal_code: str = ""
    address: str = ""

class AddressVerificationResponse(BaseModel):
    deliverable: bool
    suggestion: Optional[str] = None

def normalize_street(s: str) -> str:
    s = s.lower().strip()
    return "".join(re.findall(r'[\w]', s))

def extract_street_name(line: str) -> str:
    # Remove quotes
    line = line.strip().strip('"').strip()
    if not line:
        return ""
    
    # Street name and house number is before the first comma
    parts = line.split(",")
    street_and_number = parts[0].strip()
    
    # Split by whitespace
    tokens = street_and_number.split()
    if not tokens:
        return ""
        
    # Check if the last token is a house number (starts with a digit)
    if len(tokens) > 1 and tokens[-1][0].isdigit():
        street_name = " ".join(tokens[:-1])
    else:
        street_name = " ".join(tokens)
        
    return street_name.strip()

@router.post("/", response_model=AddressVerificationResponse)
async def verify_address(data: AddressVerificationRequest):
    postal_code = data.postal_code.strip()
    user_address = data.address.strip()
    
    if not postal_code or not user_address:
        return AddressVerificationResponse(
            deliverable=False,
            suggestion="Missing postal code or address. Please ask the customer to provide both."
        )
    
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
            
        db_streets = set()
        db_street_display_map = {}
        
        for line in lines:
            street = extract_street_name(line)
            if street:
                street_lower = street.lower()
                db_streets.add(street_lower)
                db_street_display_map[street_lower] = street

        # Extract street name from the user's input
        user_street = extract_street_name(user_address)
        if not user_street:
            return AddressVerificationResponse(
                deliverable=False,
                suggestion="Please provide a valid street name."
            )
            
        user_street_lower = user_street.lower()
        user_street_norm = normalize_street(user_street)
        
        # 1. Exact Match Check (fast)
        if user_street_lower in db_streets:
            display_name = db_street_display_map[user_street_lower]
            user_tokens = user_address.strip().split()
            house_number = ""
            if user_tokens and user_tokens[-1][0].isdigit():
                house_number = " " + user_tokens[-1]
            return AddressVerificationResponse(
                deliverable=True,
                suggestion=f"{display_name}{house_number}"
            )
            
        # 2. Fuzzy Match Check
        best_ratio = 0.0
        best_street_match = None
        
        for db_street in db_streets:
            db_street_norm = normalize_street(db_street)
            
            # Match raw lowercase
            ratio_raw = difflib.SequenceMatcher(None, user_street_lower, db_street).ratio()
            # Match normalized (no spaces/punctuation)
            ratio_norm = difflib.SequenceMatcher(None, user_street_norm, db_street_norm).ratio()
            
            max_ratio = max(ratio_raw, ratio_norm)
            if max_ratio > best_ratio:
                best_ratio = max_ratio
                best_street_match = db_street
                
        threshold = 0.65
        if best_ratio >= threshold and best_street_match:
            display_name = db_street_display_map[best_street_match]
            # Extract house number from user's address if present
            user_tokens = user_address.strip().split()
            house_number = ""
            if user_tokens and user_tokens[-1][0].isdigit():
                house_number = " " + user_tokens[-1]
                
            suggestion_address = f"{display_name}{house_number}"
            
            return AddressVerificationResponse(
                deliverable=True,
                suggestion=suggestion_address
            )
            
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
