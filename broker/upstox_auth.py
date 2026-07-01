"""
Upstox v2 OAuth2 Authentication Utility.

Use this script to generate a daily UPSTOX_ACCESS_TOKEN.
1. Run: python broker/upstox_auth.py
2. Visit the generated URL in your browser.
3. Login and authorize the app.
4. Copy the 'code' parameter from the redirect URL.
5. Paste it back into this script when prompted.
6. Copy the resulting access token into your .env file.
"""

import os
import requests
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY")
UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8080")

def generate_auth_url():
    base_url = "https://api.upstox.com/v2/login/authorization/dialog"
    params = {
        "client_id": UPSTOX_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code"
    }
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    return url

def get_access_token(code):
    url = "https://api.upstox.com/v2/login/authorization/token"
    headers = {
        'accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {
        'code': code,
        'client_id': UPSTOX_API_KEY,
        'client_secret': UPSTOX_API_SECRET,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'
    }
    
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        return response.json()
    else:
        return {"error": response.text}

if __name__ == "__main__":
    if not UPSTOX_API_KEY or not UPSTOX_API_SECRET:
        print("Error: UPSTOX_API_KEY or UPSTOX_API_SECRET not found in .env file.")
        exit(1)
        
    print("\n=== Upstox Auth Utility ===")
    auth_url = generate_auth_url()
    print(f"\n1. Visit this URL to authorize:\n{auth_url}")
    
    code = input("\n2. Enter the 'code' from the redirect URL: ").strip()
    
    if code:
        print("\n3. Exchanging code for access token...")
        result = get_access_token(code)
        
        if "access_token" in result:
            print("\n[SUCCESS] Access Token generated!")
            print(f"\nUPSTOX_ACCESS_TOKEN={result['access_token']}")
            print("\nCopy this line into your .env file.")
        else:
            print(f"\n[ERROR] Failed to get token: {result.get('error')}")
    else:
        print("\nNo code entered. Exiting.")
