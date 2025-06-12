from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
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
load_dotenv()

# Configuration - fallback removed for security
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable is required")

DATA_FILE = "data.json"

# Pydantic models for request/response
class ChatRequest(BaseModel):
    message: str

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

# Global data variable
data = {}

def load_data():
    """Load data from JSON file with error handling."""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, DATA_FILE)
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        with open(file_path, "r") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading data: {str(e)}")

def save_data(data_to_save):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data_to_save, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving data: {str(e)}")

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

def initialize_bill_agent():
    global agent_executor, data
    
    genai.configure(api_key=API_KEY)
    data = load_data()
    
    @tool("display_items")
    def display_items_tools():
        """List all items in the bill."""
        return [f"{item['name']} (x{item['quantity']}): ${item['price']}" for item in data["items"]]

    @tool("move_item")
    def move_item_tool(source_email: str, destination_email: str, item_ids: List[int]) -> str:
        """Move items from one participant to another and update balances."""
        try:
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

            save_data(data)

            return (f"Successfully moved items {item_ids} from {actual_source} to {actual_dest}. "
                    f"Updated balances - {actual_source}: ${source_participant['total_paid']:.2f}, "
                    f"{actual_dest}: ${dest_participant['total_paid']:.2f}")

        except Exception as e:
            return f"Error moving items: {str(e)}"

    @tool("divide_items")
    def divide_items_tools(percentages: str) -> str:
        """Divide items among participants based on percentage distribution."""
        try:
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
            save_data(data)
            
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
        google_api_key=API_KEY,
        temperature=0.2
    )
    
    participants_context = format_participant_context(data["participants"])
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