from fastapi import APIRouter, HTTPException, File, UploadFile
from pydantic import BaseModel
from typing import List, Optional
import sys
import os
import app
import json
from app import (
    load_data,
    save_data,
    find_closest_email,
    parse_percentage_string,
)
from receipt_cv import (
    extract_text_from_image,
    generate_structured_output,
    process_item_surcharges,
)

# Add the parent directory to the path to import from app.py
# sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Define Pydantic models for API requests/responses
class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str
    status: str
    data: Optional[dict] = None  # Add data field to include JSON structure

class MoveItemRequest(BaseModel):
    source_email: str
    destination_email: str
    item_ids: List[int]

class DivideItemsRequest(BaseModel):
    percentages: str  # Format: "email1:50%,email2:50%"

class SplitEquallyRequest(BaseModel):
    num_ways: Optional[int] = 0

def api_router_factory():
    """Factory function to create the API router after agent initialization"""
    # Make sure the agent is initialized
    if app.agent_executor is None:
        app.initialize_bill_agent()
    
    api_router = APIRouter()

    @api_router.post("/chat", response_model=ChatResponse)
    async def chat_with_agent(request: ChatRequest):
        """Main chat endpoint for interacting with the bill splitter"""
        try:
            if app.agent_executor is None:
                app.initialize_bill_agent()
            
            # Execute the agent command
            result = app.agent_executor.invoke({"input": request.message})
            
            # Load the updated data after the operation
            updated_data = load_data()
            
            return ChatResponse(
                response=result["output"], 
                status="success",
                data=updated_data  # Include the current JSON data structure
            )
            
        except Exception as e:
            # Even on error, try to return current data state
            try:
                current_data = load_data()
                return ChatResponse(
                    response=f"Error processing request: {str(e)}", 
                    status="error",
                    data=current_data
                )
            except:
                return ChatResponse(
                    response=f"Error processing request: {str(e)}", 
                    status="error",
                    data=None
                )

    @api_router.get("/participants")
    async def get_participants():
        """Get all participants and their current balances"""
        try:
            current_data = load_data()
            return {"participants": current_data["participants"]}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.get("/items")
    async def get_items():
        """Get all items in the bill"""
        try:
            current_data = load_data()
            return {"items": current_data["items"]}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.post("/move-item")
    async def move_item_endpoint(request: MoveItemRequest):
        """Move items between participants"""
        try:
            current_data = load_data()
            actual_source = find_closest_email(request.source_email, current_data["participants"])
            actual_dest = find_closest_email(request.destination_email, current_data["participants"])

            if not actual_source or not actual_dest:
                raise HTTPException(status_code=400, detail="Could not find matching email addresses")

            # Since we can't import the tool directly, we'll recreate the logic
            source_participant = next(p for p in current_data["participants"] if p["email"] == actual_source)
            dest_participant = next(p for p in current_data["participants"] if p["email"] == actual_dest)

            total_value_moved = 0

            for item_id in request.item_ids:
                item_to_move = next((item for item in source_participant["items_paid"] 
                                   if item["id"] == item_id), None)
                
                if not item_to_move:
                    raise HTTPException(status_code=400, detail=f"Item ID {item_id} not found in {actual_source}'s items.")

                source_participant["items_paid"] = [item for item in source_participant["items_paid"] 
                                                  if item["id"] != item_id]
                dest_participant["items_paid"].append(item_to_move)
                total_value_moved += item_to_move["value"]

            source_participant["total_paid"] -= total_value_moved
            dest_participant["total_paid"] += total_value_moved

            save_data(current_data)

            return {
                "message": f"Successfully moved items {request.item_ids} from {actual_source} to {actual_dest}",
                "status": "success",
                "data": current_data  # Include updated data
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.post("/split-equally")
    async def split_equally_endpoint(request: SplitEquallyRequest):
        """Split the bill equally among participants"""
        try:
            current_data = load_data()
            participants = current_data["participants"]
            num_ways = request.num_ways if request.num_ways > 0 else len(participants)
            
            if num_ways > len(participants):
                raise HTTPException(status_code=400, detail=f"Cannot split between {num_ways} people. Only {len(participants)} participants available.")
            
            base_percentage = 100.0 / num_ways
            percentages = {}
            
            for i, participant in enumerate(participants[:num_ways]):
                percentages[participant["email"]] = base_percentage
                
            percentage_str = ",".join(f"{email}:{percent}%" for email, percent in percentages.items())
            
            # Call the divide items logic
            return await divide_items_endpoint(DivideItemsRequest(percentages=percentage_str))
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.post("/divide-items")
    async def divide_items_endpoint(request: DivideItemsRequest):
        """Divide items among participants based on percentage distribution"""
        try:
            current_data = load_data()
            percentage_dict = parse_percentage_string(request.percentages)
            
            if abs(sum(percentage_dict.values()) - 100) > 0.01:
                raise HTTPException(status_code=400, detail="Percentages must sum to 100%")
                
            for participant in current_data["participants"]:
                participant["items_paid"] = []
                participant["total_paid"] = 0.0
                
            for item in current_data["items"]:
                item_value = item["nett_price"]
                
                for email, percentage in percentage_dict.items():
                    participant = next(p for p in current_data["participants"] if p["email"] == email)
                    share = {
                        "id": item["id"],
                        "value": round(item_value * (percentage / 100), 2),
                        "percentage": percentage,
                        "split_type": "percentage",
                        "original_price": item_value
                    }
                    participant["items_paid"].append(share)
                    participant["total_paid"] += share["value"]
            
            current_data["split_method"] = "divide_based"
            save_data(current_data)
            
            return {
                "message": "Bill divided successfully", 
                "status": "success",
                "data": current_data  # Include updated data
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.get("/bill-summary")
    async def get_bill_summary():
        """Get a complete summary of the current bill state"""
        try:
            current_data = load_data()
            
            # Get items
            items = [f"{item['name']} (x{item['quantity']}): ${item['price']}" for item in current_data["items"]]
            
            # Get balances
            balances = []
            for p in current_data["participants"]:
                balances.append({
                    "email": p["email"],
                    "total_paid": p["total_paid"],
                    "items_count": len(p["items_paid"])
                })
            
            return {
                "items": items,
                "participants": balances,
                "total_bill": sum(item["nett_price"] for item in current_data["items"]),
                "split_method": current_data.get("split_method", "not_set"),
                "status": "success"
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        
        
    @api_router.post("/analyze-receipt")
    async def analyze_receipt(file: UploadFile = File(...)):
        if not file:
            raise HTTPException(status_code=400, detail="No file uploaded")

        image_bytes = await file.read()

        try:
            ocr_text = extract_text_from_image(image_bytes)
            structured_output_text = generate_structured_output(ocr_text)
            structured_output = json.loads(structured_output_text)
            structured_output = process_item_surcharges(structured_output)

            print("structured_output_text", structured_output_text)
            print(json.dumps(structured_output, indent=2))

        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=500, 
                detail={
                    "raw_text": ocr_text,
                    "error": f"Invalid JSON from Gemini: {str(e)}",
                    "structured_data_raw": structured_output_text
                }
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {
            "raw_text": ocr_text,
            "structured_data": structured_output
        }
    
    return api_router