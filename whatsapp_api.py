import json
from twilio.rest import Client
from whatsapp_templates import whatsapp_templates_list
from dotenv import load_dotenv
import os

load_dotenv('.env')
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
SENDER_ID = os.getenv("TWILIO_SENDER_ID")


print("ACCOUNT_SID", ACCOUNT_SID)


class WhatsAppMessenger:
    def __init__(self, account_sid=None, auth_token=None):
        """
        Initialize WhatsApp messenger with Twilio credentials
        
        Args:
            account_sid (str): Twilio account SID
            auth_token (str): Twilio auth token
        """
        self.account_sid = ACCOUNT_SID
        self.auth_token = AUTH_TOKEN
        self.client = Client(self.account_sid, self.auth_token)
        self.default_from = SENDER_ID
    
    def send_template_message(self, content_sid, content_variables, to_number='+60163385729', fallback_body=None):
        """
        Send a WhatsApp template message
        
        Args:
            content_sid (str): Template content SID
            content_variables (str): JSON string with template variables
            to_number (str): Recipient's phone number (with country code)
            fallback_body (str, optional): Fallback message body
            
        Returns:
            str: Message SID if successful
        """
        try:
            # Parse content variables if fallback_body is None
            if fallback_body is None and content_variables:
                try:
                    variables = json.loads(content_variables)
                    # Create a bill split fallback message using the variables
                    fallback_body = f"Hello {variables['1']}! You owe ${variables['4']} to {variables['2']}. Visit {variables['3']} to pay."
                except json.JSONDecodeError:
                    fallback_body = "You have a new bill split notification. Please check for details."
            
            # Ensure the phone number is in WhatsApp format
            if not to_number.startswith('whatsapp:'):
                to_number = f'whatsapp:{to_number}'
            
            message = self.client.messages.create(
                from_=self.default_from,
                content_sid=content_sid,
                content_variables=content_variables,
                body=fallback_body,
                to=to_number
            )
            
            return message.sid
        except Exception as e:
            print(f"Error sending message: {e}")
            return None





# Example usage (only runs when script is executed directly)
if __name__ == "__main__":
    
    
    messenger = WhatsAppMessenger()
    # Or provide a custom fallback with variables
    variables = '{"1":"Li Jie", "2":"Kim", "3":"www.jomsplit.com", "4": "32.60"}'
  
    message_sid = messenger.send_template_message(
        content_sid=whatsapp_templates_list.BILL_SPLIT_TEMPLATE,
        content_variables=variables,
        to_number="+60162006426" # RAGA NUMBER LATER CHANGE
    )
    

