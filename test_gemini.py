import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("ERROR: GEMINI_API_KEY not found in .env")
    raise SystemExit

print("Gemini API key loaded successfully.")

client = genai.Client(api_key=api_key)

print("Connecting to Gemini...")

response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents="What is 10 + 20? Reply with only the number."
)

print("Gemini response:")
print(response.text)