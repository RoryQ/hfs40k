import os
import re
import requests
import json
import base64
import yaml
import time
from PIL import Image
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

def fetch_itunes_cover(artist, album):
    if artist == 'Unknown' or album == 'Unknown': return None
    # Clean query
    q_art = re.sub(r'\(.*?\)|\[.*?\]', '', artist).strip()
    q_alb = re.sub(r'\(.*?\)|\[.*?\]', '', album).strip()
    query = f"{q_art} {q_alb}"
    url = f"https://itunes.apple.com/search?term={requests.utils.quote(query)}&entity=album&limit=1"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data['resultCount'] > 0:
            return data['results'][0].get('artworkUrl100').replace('100x100bb', '600x600bb')
    except:
        pass
    return None

def sanitize_image(image_filename, artist, album):
    image_path = os.path.join(IMAGES_DIR, image_filename)
    if not os.path.exists(image_path):
        return "MISSING"

    # 1. Vision Audit
    try:
        base64_image = encode_image(image_path)
        prompt = f"Analyze this image for the album '{artist} - {album}'. Verify if it is the correct cover. Respond with a JSON object: {{\"status\": \"OK\", \"is_nsfw\": false, \"is_correct\": true, \"reason\": \"...\"}}. If the image is sexually explicit, set is_nsfw to true. If it is the wrong album or garbage, set is_correct to false."

        response = client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{base64_image}"}}]}],
            response_format={"type": "json_object"}
        )
        res = json.loads(response.choices[0].message.content)
        
        if res.get('is_nsfw') or not res.get('is_correct'):
            reason = res.get('reason', 'Unknown reason')
            print(f"  [BAD] {image_filename} ({artist} - {album}): {reason}")
            
            # 2. Auto Repair
            new_url = fetch_itunes_cover(artist, album)
            if new_url:
                print(f"    -> Repairing {image_filename} with iTunes cover...")
                resp = requests.get(new_url, stream=True, timeout=20)
                if resp.status_code == 200:
                    with Image.open(resp.raw) as new_img:
                        new_img.save(image_path, format="WEBP", quality=85)
                    return "REPAIRED"
            return "FLAGGED"
        return "OK"
    except Exception as e:
        if "429" in str(e):
            return "RATE_LIMIT"
        return f"ERROR: {str(e)}"

def run_sanitizer(limit=None):
    files = [f for f in os.listdir(POSTS_DIR) if f.endswith('.md')]
    
    # 1. Map images to metadata
    image_map = {}
    for f_name in files:
        path = os.path.join(POSTS_DIR, f_name)
        with open(path, 'r') as f:
            content = f.read()
            parts = content.split('---', 2)
            if len(parts) < 3: continue
            fm = yaml.safe_load(parts[1])
            if fm.get('artist') == 'Site Announcement': continue
            
            artist = fm.get('artist', 'Unknown')
            album = fm.get('album', 'Unknown')
            
            img_matches = re.findall(r'/images/([a-f0-9]+\.webp)', parts[2])
            for img in img_matches:
                if img not in image_map:
                    image_map[img] = (artist, album)

    all_images = list(image_map.keys())
    print(f"Unique images to audit: {len(all_images)}")
    if limit:
        all_images = all_images[:limit]

    # Process in chunks to handle rate limits gracefully
    chunk_size = 20
    for i in range(0, len(all_images), chunk_size):
        chunk = all_images[i:i+chunk_size]
        print(f"Processing chunk {i//chunk_size + 1}/{(len(all_images)-1)//chunk_size + 1}...")
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(sanitize_image, img, image_map[img][0], image_map[img][1]): img for img in chunk}
            for future in as_completed(futures):
                res = future.result()
                img = futures[future]
                if res == "RATE_LIMIT":
                    print("  Hit rate limit, backing off...")
                    time.sleep(10)
                elif res != "OK":
                    print(f"  Result for {img}: {res}")
        
        time.sleep(2) # Breather between chunks

if __name__ == "__main__":
    # WARNING: Auditing all 1700+ images will take time and credits. 
    # I'll set a healthy limit for now, or you can remove the limit to do the full run.
    run_sanitizer(limit=None)
