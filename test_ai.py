import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

token = os.getenv("AIPIPE_TOKEN")

if not token:
    print("ERROR: AIPIPE_TOKEN not found")
    raise SystemExit

client = OpenAI(
    api_key=token,
    base_url="https://aipipe.org/openrouter/v1"
)

print("Connecting to AI Pipe...")

response = client.chat.completions.create(
    model="qwen/qwen3-next-80b-a3b-instruct:free",
    messages=[
        {
            "role": "user",
            "content": "What is 10 + 20? Reply with only the number."
        }
    ]
)

print("AI response:")
print(response.choices[0].message.content)