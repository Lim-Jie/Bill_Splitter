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
 If you see "ROUNDING ADJ" or anything equivalent with amount X.XX, then:
       - rounding_adj = "X.XX" 
    
6. Extract all items with details
7. Set "split_method" to "item_based"
8. Assign all items to the first participant initially
9. Set the location name to the area (city name example : Kuala Lumpur), leave empty if none found. If address of the shop/lot number, list it under "address" variable else leave empty.
10. After all has been done, rate the confidence score of the overall information extracted. It can incude how blur, covered/shadow, unproper lighting, poor contrast of the image, which affect the generated output. It should be written in notes
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
  "rounding_adj": 0.00,
  "paid_by": "",
  "location_name": "",
  "address": "",
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
  "notes": "Brief description of the confidence of scanning etc",
  "confidence_score":0.0
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
    service_charge_amount = structured_output.get('service_charge_amount')
    tax_rate_amount = structured_output.get('tax_rate_amount')
    
    
    if ((service_charge_rate == 0) and (service_charge_amount != 0)):
        subtotal = structured_output.get('subtotal_amount', 0)
        if subtotal > 0:
            structured_output['service_charge_rate'] = round(service_charge_amount/subtotal, 4)
            service_charge_rate = structured_output['service_charge_rate']
            print("Tax rate found but no tax amount... correcting from " + str(service_charge_rate) + " to " + str(structured_output['service_charge_rate']))
            
    elif ((tax_rate == 0) and (tax_rate_amount !=0)):
        subtotal = structured_output.get('subtotal_amount', 0)
        if subtotal > 0:
            structured_output['tax_rate'] = round(structured_output.get('tax_amount')/subtotal, 4)
            tax_rate = structured_output['tax_rate']
            print("Tax rate found but no tax amount... correcting from " + str(tax_rate) + " to " + str(structured_output['tax_rate']))

        
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

def initialize_participants(structured_output, participants_list, email):
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
                total_paid += (item["nett_price"] * item["quantity"])
            
            participants.append({
                "email": participant["email"],
                "total_paid": round(total_paid, 2),  # Round to 2 decimal places
                "items_paid": items_paid
            })
        else:  # Other participants get empty items
            participants.append({
                "email": participant["email"],
                "total_paid": 0.0,
                "items_paid": []
            })
    
    if email:
        print("Email: ", email , "found")
        print("paid_by has been revised to: ", email)
        structured_output["paid_by"]=str(email)
    else:
         structured_output["paid_by"]="you@example.com"
    
    # Add participants to structured output
    structured_output["participants"] = participants
    structured_output["split_method"] = "not_set"
    
    return structured_output

def evaluate_and_adjust_bill(structured_output):
    """
    Evaluate if the bill is correctly split and adjust the first item if needed
    """
    items = structured_output.get("items", [])
    if not items:
        return structured_output
    
    # Calculate total nett price from items
    items_nett_price = sum(item["nett_price"] * item["quantity"] for item in items)
    
    # Get the actual nett amount from the bill
    nett_amount = structured_output.get("nett_amount", 0)
    rounding_adj = structured_output.get("rounding_adj", 0)
    
    # Calculate error difference (difference between items total and nett_amount)
    error_diff = nett_amount - (rounding_adj + items_nett_price)
    
    # Get the first item
    first_item = items[0]
    
    # Add rounding_adj to first item's nett_price if it exists
    if rounding_adj != 0:
        first_item["nett_price"] = round(first_item["nett_price"] + rounding_adj, 2)
        first_item["rounding_adj"] = rounding_adj
        print(f"Added rounding_adj: {rounding_adj} to first item")
    
    # Add error_diff to first item's nett_price and add attribute if it exists
    if error_diff != 0:
        first_item["nett_price"] = round(first_item["nett_price"] + error_diff, 2)
        first_item["error_diff"] = round(error_diff, 2)
        print(f"Added error_diff: {error_diff} to first item")
        print(f"nett_amount ${nett_amount}")
        print(f"rounding_adj + items_nett_price ${rounding_adj + items_nett_price}")
    
    # Recalculate items_nett_price for verification
    final_items_nett_price = sum(item["nett_price"] * item["quantity"] for item in items)
    
    if abs(final_items_nett_price - nett_amount) < 0.01:
        print("THE BILL IS CORRECTLY SPLIT")
        print(f"Final items_nett_price: {final_items_nett_price}")
        print(f"Bill nett_amount: {nett_amount}")
    else:
        print("THE BILL IS NOT CORRECTLY SPLIT")
        print(f"Final items_nett_price: {final_items_nett_price}")
        print(f"Bill nett_amount: {nett_amount}")
        print(f"Remaining difference: {nett_amount - final_items_nett_price}")
    
    print(f"Adjusted first item nett_price to: {first_item['nett_price']}")
    
    return structured_output