from fastapi import APIRouter, HTTPException, File, UploadFile, Form
from pydantic import BaseModel
from typing import List, Optional
import json
import app
from receipt_cv import (
    extract_text_from_image,
    generate_structured_output,
    process_item_surcharges,
    initialize_participants,
    evaluate_and_adjust_bill
)

# Define Pydantic models for API requests/responses
class ChatRequest(BaseModel):
    message: str
    input: dict  # Required field for JSON structure

class ChatResponse(BaseModel):
    response: str
    status: str
    data: Optional[dict] = None  # Add data field to include JSON structure

class MoveItemRequest(BaseModel):
    source_email: str
    destination_email: str
    item_ids: List[int]
    input: dict  # Required field for JSON structure

class DivideItemsRequest(BaseModel):
    percentages: str  # Format: "email1:50%,email2:50%"
    input: dict  # Required field for JSON structure

class SplitEquallyRequest(BaseModel):
    num_ways: Optional[int] = 0
    input: dict  # Required field for JSON structure

def api_router_factory():
    """Factory function to create the API router"""
    api_router = APIRouter()

    @api_router.post("/chat", response_model=ChatResponse)
    async def chat_with_agent(request: ChatRequest):
        """Main chat endpoint for interacting with the bill splitter"""
        try:
            if not request.input:
                return ChatResponse(
                    response="Error: 'input' field is required with the bill data structure.", 
                    status="error",
                    data=None
                )
            
            # Initialize agent with the provided data
            app.initialize_bill_agent(request.input)
            
            # Execute the agent command
            result = app.agent_executor.invoke({"input": request.message})
            
            # Get the updated data from memory
            updated_data = app.get_current_data()
            
            # Evaluate the splitting and assign the buffer randomly
            difference = app.evaluate_chat_splitting(updated_data)
            
            return ChatResponse(
                response=result["output"], 
                status="success",
                data=updated_data,
                difference=difference,
            )
            
        except Exception as e:
            return ChatResponse(
                response=f"Error processing request: {str(e)}", 
                status="error",
                data=request.input if request.input else None,
                difference=0
            )

    @api_router.post("/participants")
    async def get_participants(request: dict):
        """Get all participants and their current balances"""
        try:
            if not request.get("input"):
                raise HTTPException(status_code=400, detail="'input' field is required")
            
            current_data = request["input"]
            return {"participants": current_data["participants"]}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.post("/items")
    async def get_items(request: dict):
        """Get all items in the bill"""
        try:
            if not request.get("input"):
                raise HTTPException(status_code=400, detail="'input' field is required")
            
            current_data = request["input"]
            return {"items": current_data["items"]}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.post("/move-item")
    async def move_item_endpoint(request: MoveItemRequest):
        """Move items between participants"""
        try:
            if not request.input:
                raise HTTPException(status_code=400, detail="'input' field is required")
            
            app.set_current_data(request.input)
            current_data = app.get_current_data()
            
            actual_source = app.find_closest_email(request.source_email, current_data["participants"])
            actual_dest = app.find_closest_email(request.destination_email, current_data["participants"])

            if not actual_source or not actual_dest:
                raise HTTPException(status_code=400, detail="Could not find matching email addresses")

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

            app.update_current_data(current_data)

            return {
                "message": f"Successfully moved items {request.item_ids} from {actual_source} to {actual_dest}",
                "status": "success",
                "data": app.get_current_data()
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.post("/split-equally")
    async def split_equally_endpoint(request: SplitEquallyRequest):
        """Split the bill equally among participants"""
        try:
            if not request.input:
                raise HTTPException(status_code=400, detail="'input' field is required")
            
            app.set_current_data(request.input)
            current_data = app.get_current_data()
            
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
            divide_request = DivideItemsRequest(percentages=percentage_str, input=request.input)
            return await divide_items_endpoint(divide_request)
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.post("/divide-items")
    async def divide_items_endpoint(request: DivideItemsRequest):
        """Divide items among participants based on percentage distribution"""
        try:
            if not request.input:
                raise HTTPException(status_code=400, detail="'input' field is required")
            
            app.set_current_data(request.input)
            current_data = app.get_current_data()
            
            percentage_dict = app.parse_percentage_string(request.percentages)
            
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
            app.update_current_data(current_data)
            
            return {
                "message": "Bill divided successfully", 
                "status": "success",
                "data": app.get_current_data()
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @api_router.post("/bill-summary")
    async def get_bill_summary(request: dict):
        """Get a complete summary of the current bill state"""
        try:
            if not request.get("input"):
                raise HTTPException(status_code=400, detail="'input' field is required")
            
            current_data = request["input"]
            
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
    async def analyze_receipt(file: UploadFile = File(...), participants: str = Form(...), email: Optional[str] = Form(None)):
        # Parse the JSON string
        participants_list = json.loads(participants)
        
        # Validate participants format
        for participant in participants_list:
            if not isinstance(participant, dict) or "name" not in participant or "email" not in participant:
                raise HTTPException(
                    status_code=400,
                    detail="Each participant must be an object with 'name' and 'email' fields"
                )
        
        image_bytes = await file.read()

        try:
            # Extract text from image via OCR, create JSON structure, parse string structure to JSON object
            ocr_text = extract_text_from_image(image_bytes)
            structured_output_text = generate_structured_output(ocr_text)
            structured_output = json.loads(structured_output_text)
            
            
            # Calculate total surcharge rate and apply it to each item
            structured_output = process_item_surcharges(structured_output)
            
            #Evaluate whether the items_nett price equals to the nett_amount of the bill
            structured_output = evaluate_and_adjust_bill(structured_output)

            # Pass participants_list to initialize_participants
            structured_output = initialize_participants(structured_output, participants_list, email)
            
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