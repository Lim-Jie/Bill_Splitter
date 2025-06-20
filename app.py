from fastapi import HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
import os
import json
import google.generativeai as genai
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.tools import tool
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate
from difflib import get_close_matches
from LLM_Context import system_message

# Load environment variables
load_dotenv('.env.local')

# Configuration - fallback removed for security
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is required")

# Pydantic models for request/response
class ChatRequest(BaseModel):
    message: str
    input: dict  # Required field to receive JSON structure

class ChatResponse(BaseModel):
    response: str
    status: str

class MoveItemRequest(BaseModel):
    source_email: str
    destination_email: str
    item_ids: List[int]

class DivideItemsRequest(BaseModel):
    percentages: str  # Format: "email1:50%,email2:50%"

class SplitEquallyRequest(BaseModel):
    num_ways: Optional[int] = 0

# Global data variable - this is now the only source of truth
current_data = {}

def get_current_data():
    """Get the current data stored in memory."""
    if not current_data:
        raise HTTPException(status_code=400, detail="No data provided. Please include 'input' field in your request.")
    return current_data

def set_current_data(input_data):
    """Set the current data from API input."""
    global current_data
    if not input_data:
        raise HTTPException(status_code=400, detail="Input data cannot be empty.")
    current_data = input_data.copy()  # Make a copy to avoid reference issues

def update_current_data(updated_data):
    """Update the current data in memory."""
    global current_data
    current_data = updated_data

def find_closest_email(email: str, participants: List[Dict]) -> str:
    valid_emails = [p["email"] for p in participants]
    matches = get_close_matches(email.lower(), valid_emails, n=1, cutoff=0.6)
    return matches[0] if matches else None

def format_participant_context(participants):
    """Format participants data for the LLM context"""
    valid_emails = [p["email"] for p in participants]
    all_valid_items = set()
    
    for p in participants:
        for item in p["items_paid"]:
            if "id" in item:
                all_valid_items.add(item["id"])
    
    return (
        "\n\nVALID EMAIL ADDRESSES:\n" +
        "\n".join(f"- {email}" for email in valid_emails) + 
        "\n\nVALID ITEMS: \n" + 
        "\n".join(f"ID: {items}" for items in sorted(all_valid_items))
    )

def parse_percentage_string(percentage_str: str) -> Dict[str, float]:
    """Convert percentage string to dictionary format."""
    try:
        cleaned_str = percentage_str.replace(" ", "")
        assignments = cleaned_str.split(",")
        
        percentages = {}
        for assignment in assignments:
            email, percentage = assignment.split(":")
            percentage = float(percentage.rstrip("%"))
            percentages[email] = percentage
            
        return percentages
    except Exception as e:
        raise ValueError(f"Invalid percentage format. Expected format: 'email:XX%,email:YY%'. Error: {e}")

# Initialize agent once
agent_executor = None

def initialize_bill_agent(input_data):
    """Initialize the bill agent with the provided data."""
    global agent_executor
    
    if not input_data:
        raise HTTPException(status_code=400, detail="Input data is required to initialize the agent.")
    
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Set the current data
    set_current_data(input_data)
    
    @tool("display_items")
    def display_items_tools():
        """List all items in the bill."""
        data = get_current_data()
        return [f"{item['name']} (x{item['quantity']}): ${item['nett_price']}" for item in data["items"]]

    @tool("move_item")
    def move_item_tool(source_email: str, destination_email: str, item_ids: List[int]) -> str:
        """Move items from one participant to another and update balances."""
        try:
            data = get_current_data()
            actual_source = find_closest_email(source_email, data["participants"])
            actual_dest = find_closest_email(destination_email, data["participants"])

            if not actual_source or not actual_dest:
                return "Could not find matching email addresses among participants."

            source_participant = next(p for p in data["participants"] if p["email"] == actual_source)
            dest_participant = next(p for p in data["participants"] if p["email"] == actual_dest)

            total_value_moved = 0

            for item_id in item_ids:
                item_to_move = next((item for item in source_participant["items_paid"] 
                                   if item["id"] == item_id), None)
                
                if not item_to_move:
                    return f"Item ID {item_id} not found in {actual_source}'s items."

                # Remove item from source
                source_participant["items_paid"] = [item for item in source_participant["items_paid"] 
                                                  if item["id"] != item_id]
                
                # Check if destination already has this item ID
                existing_item = next((item for item in dest_participant["items_paid"] 
                                    if item["id"] == item_id), None)
                
                if existing_item:
                    # Consolidate: add values and percentages
                    existing_item["value"] += item_to_move["value"]
                    existing_item["percentage"] += item_to_move["percentage"]
                else:
                    # Add new item to destination
                    dest_participant["items_paid"].append(item_to_move)
                
                total_value_moved += item_to_move["value"]

            source_participant["total_paid"] -= total_value_moved
            dest_participant["total_paid"] += total_value_moved

            # Update the current data
            update_current_data(data)

            return (f"Successfully moved items {item_ids} from {actual_source} to {actual_dest}. "
                    f"Updated balances - {actual_source}: ${source_participant['total_paid']:.2f}, "
                    f"{actual_dest}: ${dest_participant['total_paid']:.2f}")

        except Exception as e:
            return f"Error moving items: {str(e)}"

    @tool("divide_items")
    def divide_items_tools(percentages: str) -> str:
        """Divide items among participants based on percentage distribution."""
        try:
            data = get_current_data()
            percentage_dict = parse_percentage_string(percentages)
            
            if abs(sum(percentage_dict.values()) - 100) > 0.01:
                return "Error: Percentages must sum to 100%"
                
            for participant in data["participants"]:
                participant["items_paid"] = []
                participant["total_paid"] = 0.0
                
            for item in data["items"]:
                item_value = item["nett_price"]
                
                for email, percentage in percentage_dict.items():
                    participant = next(p for p in data["participants"] if p["email"] == email)
                    share = {
                        "id": item["id"],
                        "value": round(item_value * (percentage / 100), 2),
                        "percentage": percentage,
                        "split_type": "percentage",
                        "original_price": item_value
                    }
                    participant["items_paid"].append(share)
                    participant["total_paid"] += share["value"]
            
            data["split_method"] = "divide_based"
            update_current_data(data)
            
            result = "Bill divided by percentages:\n"
            for p in data["participants"]:
                result += f"\n{p['email']}: ${p['total_paid']:.2f}"
                for item in p["items_paid"]:
                    result += f"\n  - Item {item['id']}: {item['percentage']}% = ${item['value']:.2f}"
            
            return result

        except Exception as e:
            return f"Error dividing items: {str(e)}"

    @tool("split_equally")
    def split_equally_tool(num_ways: int = 0) -> str:
        """Split the bill equally among the specified number of participants."""
        try:
            data = get_current_data()
            participants = data["participants"]
            if num_ways == 0:
                num_ways = len(participants)
            
            if num_ways > len(participants):
                return f"Cannot split between {num_ways} people. Only {len(participants)} participants available."
            
            base_percentage = 100.0 / num_ways
            percentages = {}
            
            for i, participant in enumerate(participants[:num_ways]):
                percentages[participant["email"]] = base_percentage
                
            percentage_str = ",".join(f"{email}:{percent}%" for email, percent in percentages.items())
            return divide_items_tools(percentage_str)
            
        except Exception as e:
            return f"Error splitting bill: {str(e)}"

    tools = [display_items_tools, move_item_tool, divide_items_tools, split_equally_tool]
    
    model = ChatGoogleGenerativeAI(
        model="gemini-1.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.2
    )
    
    participants_context = format_participant_context(current_data["participants"])
    combined_context = f"{system_message}\n{participants_context}"
    
    # Create prompt template for newer langchain version
    prompt = ChatPromptTemplate.from_messages([
        ("system", combined_context),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    
    # Create agent and executor
    agent = create_tool_calling_agent(model, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# This file now only contains core logic and utility functions
# All API endpoints are handled by main.py and routes.py