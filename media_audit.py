import os
import re
import json
import base64
import requests
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

DB_NAME = "tracking.db"
POSTS_DIR = "src/content/posts"
IMAGES_DIR = "public/images"

# Configure OpenRouter
api_key = os.environ.get("OPENROUTER_API_KEY")
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def audit_image(image_filename, artist, album):
    image_path = os.path.join(IMAGES_DIR, image_filename)
    if not os.path.exists(image_path):
        return None
        
    base64_image = encode_image(image_path)
    
    prompt = f"This is supposed to be the album cover for '{artist} - {album}'. Does this image match that description? Or is it unrelated, inappropriate, NSFW (explicit), or a generic 'image not found' placeholder? Respond strictly with a JSON object: {{\"status\": \"OK\" or \"WRONG\" or \"NSFW\", \"reason\": \"...\"}}"

    try:
        response = client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/webp;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return {"filename": image_filename, "artist": artist, "album": album, **result}
    except Exception as e:
        # print(f"Audit Error for {image_filename}: {e}")
        return {"filename": image_filename, "status": "ERROR", "reason": str(e)}

def run_audit(limit=10):
    files = [f for f in os.listdir(POSTS_DIR) if f.endswith('.md')]
    
    tasks = []
    for f_name in files:
        path = os.path.join(POSTS_DIR, f_name)
        with open(path, 'r') as f:
            content = f.read()
            parts = content.split('---', 2)
            if len(parts) < 3: continue
            fm = yaml.safe_load(parts[1])
            artist = fm.get('artist', 'Unknown')
            album = fm.get('album', 'Unknown')
            
            # Find local images
            found_images = re.findall(r'/images/([a-f0-9]+\.webp)', parts[2])
            for img in found_images:
                tasks.append((img, artist, album))
    
    # Prioritize Shellac if found
    tasks.sort(key=lambda x: 1 if "Shellac" in x[1] else 0, reverse=True)
    
    if limit:
        tasks = tasks[:limit]
        
    print(f"Auditing {len(tasks)} images...")
    
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(audit_image, img, artist, album): img for img, artist, album in tasks}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                print(f"[{res['status']}] {res['filename']} ({res['artist']} - {res['album']}): {res.get('reason', '')}")

    with open("audit_results.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_audit(limit=20) # Test with 20
