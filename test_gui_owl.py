import base64
import requests

API_URL = "http://127.0.0.1:8080/v1/chat/completions"
IMAGE_PATH = r"test1.jpg"

with open(IMAGE_PATH, "rb") as f:
    image_b64 = base64.b64encode(f.read()).decode("utf-8")

payload = {
    "model": "gui-owl",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_b64}"
                    }
                },
                {
                    "type": "text",
                    "text": "Describe this image briefly."
                }
            ]
        }
    ],
    "temperature": 0.1,
    "max_tokens": 128,
    "stream": False
}

r = requests.post(API_URL, json=payload, timeout=180)
print("STATUS:", r.status_code)
print(r.text)