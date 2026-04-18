import os
import re
import requests
import yaml
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

POSTS_DIR = "src/content/posts"
COVERS_DIR = "public/images/covers"
BASE_PATH = "/holyfuckingshit40000"

def fetch_itunes_cover_url(artist, album, title, session):
    search_terms = []
    
    # Pre-process strings
    def clean(s):
        if not s: return ""
        s = re.sub(r'\(.*?\)|\[.*?\]', '', s)
        s = re.sub(r'flac|v0|320|remastered|reissue|lossless', '', s, flags=re.I)
        return s.strip()

    artist = clean(artist)
    album = clean(album)
    title = clean(title)

    if artist and album:
        search_terms.append(f"{artist} {album}")
        search_terms.append(f"{album} {artist}")
    
    if title:
        search_terms.append(title)
        # Try split title if it has -
        if ' - ' in title:
            parts = title.split(' - ')
            search_terms.append(f"{parts[0]} {parts[1]}")

    # Remove duplicates
    search_terms = list(dict.fromkeys(search_terms))

    for term in search_terms:
        if len(term) < 3: continue
        url = f"https://itunes.apple.com/search?term={quote(term)}&entity=album&limit=1"
        try:
            response = session.get(url, timeout=5)
            data = response.json()
            if data['resultCount'] > 0:
                artwork = data['results'][0].get('artworkUrl100')
                if artwork:
                    return artwork.replace('100x100bb', '1000x1000bb')
        except:
            pass
    return None

def process_post(filename, session):
    path = os.path.join(POSTS_DIR, filename)
    slug = os.path.splitext(filename)[0]
    
    # Skip if already has an official cover
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    if "/images/covers/" in content:
        return False

    parts = content.split('---', 2)
    if len(parts) < 3: return False
    
    try:
        fm = yaml.safe_load(parts[1])
        if fm.get('artist') == 'Site Announcement': return False
        
        artist = fm.get('artist')
        album = fm.get('album')
        title = fm.get('title')
        
        cover_url = fetch_itunes_cover_url(artist, album, title, session)
        if not cover_url:
            return False
            
        local_filename = f"{slug}.webp"
        local_path = os.path.join(COVERS_DIR, local_filename)
        
        resp = session.get(cover_url, stream=True, timeout=20)
        if resp.status_code == 200:
            with Image.open(resp.raw) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(local_path, format="WEBP", quality=85, optimize=True)
            
            local_url = f"{BASE_PATH}/images/covers/{local_filename}"
            new_body = parts[2]
            
            # More aggressive regex to find ANY markdown or HTML image in the body
            # We want to replace the first one.
            patterns = [
                r'\[?!\[.*?\]\(.*?\.\w+\)\]?\(.*?\)', # Markdown image with optional link
                r'!\[.*?\]\(.*?\.\w+\)',              # Simple markdown image
                r'<img.*?>'                           # HTML img tag
            ]
            
            replacement = f"[![]({local_url})]({local_url})"
            
            found = False
            for p in patterns:
                if re.search(p, new_body, re.I):
                    new_body = re.sub(p, replacement, new_body, count=1, flags=re.I)
                    found = True
                    break
            
            if found:
                with open(path, 'w', encoding='utf-8') as f_out:
                    f_out.write(f"---{parts[1]}---{new_body}")
                return True
    except Exception:
        pass
    return False

def run():
    if not os.path.exists(COVERS_DIR):
        os.makedirs(COVERS_DIR)
        
    files = [f for f in os.listdir(POSTS_DIR) if f.endswith('.md')]
    print(f"Deep fetching official covers for {len(files)} posts...")
    
    session = requests.Session()
    # Add retries
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('http://', HTTPAdapter(max_retries=retries))
    session.mount('https://', HTTPAdapter(max_retries=retries))

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = {executor.submit(process_post, f, session): f for f in files}
        
        count = 0
        success_count = 0
        for future in as_completed(futures):
            if future.result():
                success_count += 1
            count += 1
            if count % 50 == 0:
                print(f"Progress: {count}/{len(files)}... (Updated {success_count} more)")
                
    print(f"\nDone. Successfully added {success_count} more official iTunes covers.")

if __name__ == "__main__":
    run()
