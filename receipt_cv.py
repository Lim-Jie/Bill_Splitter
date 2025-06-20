from google.cloud import vision
import google.generativeai as genai
import os
import re
from dotenv import load_dotenv
from pathlib import Path
 

# # Load environment variables
# load_dotenv(dotenv_path=".env")

# # Set up Google API credentials
# os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_PATH')
# genai.configure(api_key=os.getenv('GEMINI_API_KEY'))


def loadENVJson():
    filename = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_FILENAME')

    # Get current directory and build full path
    current_dir = Path.cwd()
    credentials_path = current_dir / filename

    # Check if file exists and set environment variable
    if credentials_path.exists():
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(credentials_path)
        print(f"Credentials loaded: {credentials_path}")
    else:
        raise FileNotFoundError(f"Credentials file not found: {credentials_path}")

# Load environment variables
load_dotenv(dotenv_path=".env")
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
loadENVJson()


PROMPT_TEMPLATE = """
You are a helpful assistant that extracts structured data from OCR-detected receipt text.

Given the raw receipt text below, extract and format the data according to these specifications:

1. Generate a bill_id with format "BILL" + current date (YYYYMMDD) + "-001"
2. Extract restaurant/store name, date, and time
3. Classify the receipt into one of these categories: "Food", "Education", "Transportation", "Clothing", "Entertainment", or "Other"
   - Food: restaurants, cafes, grocery stores, food delivery
   - Education: books, courses, tuition, school supplies
   - Transportation: gas, ride-sharing, public transit, parking
   - Clothing: apparel, shoes, accessories
   - Entertainment: movies, events, subscriptions
   - Other: anything that doesn't fit the above categories
4. Calculate tax_rate and service_charge_rate from range (0.00 to 1.00 representing percentage) 
    - Example: 6 percent refers to 0.06 and 10 percent refers to 0.10
    - If there is no service charge or TAX including (GST)(SST) then return 0.00 for each respectively

5. Assign the tax_amount and service_charge_amount based listed on the bill respectively. 
    - If there are no tax_rate or service_charge_rate then there shouldn't be tax_amount or service_charge_amount RESPECTIVELY.
    - If you see "SERVICE CHARGE (5%)"or anything equivalent with amount X.XX, then:
       - service_charge_rate = 0.05
       - service_charge_amount = X.XX
       - tax_rate = 0.00 (not mentioned)
       - tax_amount = 0.00 (not mentioned)
 - If you see "TAX_RATE (6%)"or anything equivalent with amount X.XX, then:
       - service_charge_rate = 0.00 (not mentioned)
       - service_charge_amount = 0.00 (not mentioned)
       - tax_rate =  0.06 
       - tax_amount = X.XX
 - If you see "SERVICE CHARGE (5%)"or anything equivalent with amount Y.YY and also see "TAX_RATE (6%)"or anything equivalent with amount X.XX, then:
       - service_charge_rate = 0.05
       - service_charge_amount = Y.YY
       - tax_rate =  0.06 
       - tax_amount = X.XX
    
6. Extract all items with details
7. Set "split_method" to "item_based"
8. Create a default participant using the first email found (or "alice@example.com" if none)
9. Assign all items to the first participant initially

Return the data in EXACTLY this JSON format:
{
  "bill_id": "BILL20250606-001",
  "name": "Restaurant Name",
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "category": "Food",
  "tax_rate": 0.00,
  "service_charge_rate": 0.00,
  "subtotal_amount": 0.00,
  "tax_amount": 0.00,
  "service_charge_amount": 0.00,
  "nett_amount": 0.00,
  "paid_by": "alice@example.com",
  "items": [
    {
      "id": 1,
      "name": "Item Name",
      "price": 0.00,
      "tax_amount": 0.00,
      "nett_price": 0.00,
      "quantity": 1,
      "consumed_by": []
    }
  ],
  "split_method": "item_based",
  "notes": "Brief description of any special charges or notes"
}

Raw text:
---
{{RECEIPT_TEXT}}
---
"""

def extract_text_from_image(image_bytes):
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    response = client.text_detection(image=image)
    if response.error.message:
        raise Exception(response.error.message)
    return response.text_annotations[0].description if response.text_annotations else ""

def clean_json_response(text):
    """
    Removes markdown-style formatting (like ```json and ```) from the response.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()

def generate_structured_output(ocr_text):
    model = genai.GenerativeModel(model_name="models/gemini-2.0-flash-lite")
    prompt = PROMPT_TEMPLATE.replace("{{RECEIPT_TEXT}}", ocr_text)
    response = model.generate_content(prompt)
    cleaned_response = clean_json_response(response.text)
    return cleaned_response

def process_item_surcharges(structured_output):
    """
    Calculate total surcharge rate and apply it to each item
    """
    # Extract rates, handling None values
    service_charge_rate = structured_output.get('service_charge_rate') or 0
    tax_rate = structured_output.get('tax_rate') or 0
    
    # Calculate total surcharge rate
    total_surcharge_rate = service_charge_rate + tax_rate
    
    # Add the new variable to the JSON
    structured_output['total_surcharge_rate'] = total_surcharge_rate
    
    # Apply surcharge to each item
    for item in structured_output.get('items', []):
        item_price = item.get('price', 0)
        
        # Calculate tax_amount as price * total_surcharge_rate
        item['tax_amount'] = round(item_price * total_surcharge_rate, 2)
        
        # Calculate nett_price as price + tax_amount
        item['nett_price'] = round(item_price + item['tax_amount'], 2)
    
    return structured_output

def initialize_participants(structured_output, participants_list):
    """Initialize participants with the provided participant data"""
    
    # Create participants structure
    participants = []
    
    for i, participant in enumerate(participants_list):
        if i == 0:  # First participant gets all items
            items_paid = []
            total_paid = 0.0
            
            # Assign all items to first participant with 100% responsibility
            for item in structured_output.get("items", []):
                item_payment = {
                    "id": item["id"],
                    "percentage": 100,
                    "value": item["nett_price"]
                }
                items_paid.append(item_payment)
                total_paid += item["nett_price"]
            
            participants.append({
                "email": participant["email"],
                "total_paid": total_paid,
                "items_paid": items_paid
            })
        else:  # Other participants get empty items
            participants.append({
                "email": participant["email"],
                "total_paid": 0.0,
                "items_paid": []
            })
    
    # Add participants to structured output
    structured_output["participants"] = participants
    structured_output["split_method"] = "not_set"
    
    return structured_output