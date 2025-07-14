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