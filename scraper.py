import sqlite3
import requests
import os
import re
import time
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import yaml
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_NAME = "tracking.db"
POSTS_DIR = "posts"
BASE_URL = "holyfuckingshit40000.blogspot.com"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_url TEXT UNIQUE,
            archived_url TEXT,
            timestamp TEXT,
            status TEXT DEFAULT 'PENDING',
            http_status INTEGER,
            error_message TEXT,
            local_path TEXT
        )
    ''')
    conn.commit()
    conn.close()

def discover_urls():
    print("Discovering URLs via Wayback Machine CDX API...")
    # Filter for 200 status codes to avoid archived 404s
    cdx_url = f"http://web.archive.org/cdx/search/cdx?url={BASE_URL}/*&output=json&fl=original,timestamp,statuscode,digest&filter=statuscode:200"
    response = requests.get(cdx_url)
    if response.status_code != 200:
        print(f"Failed to fetch CDX data: {response.status_code}")
        return

    data = response.json()
    if not data or len(data) < 2:
        print("No URLs found in CDX API.")
        return

    headers = data[0]
    rows = data[1:]
    
    # Sort by timestamp DESC to get the latest snapshot when deduplicating
    rows.sort(key=lambda x: x[1], reverse=True)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM pages') # Clear previous messy results
    
    count = 0
    for row in rows:
        original_url = row[0]
        timestamp = row[1]
        
        # Normalize URL: remove :80, remove www., ensure http://
        norm_url = original_url.replace(":80", "").replace("www.", "")
        if norm_url.startswith("https://"):
            norm_url = norm_url.replace("https://", "http://", 1)
        
        # Refined filter for blog post URLs
        if (".html" in norm_url and 
            re.search(r'/\d{4}/\d{2}/', norm_url) and 
            "?" not in norm_url and 
            "/archive" not in norm_url):
            
            try:
                cursor.execute('INSERT OR IGNORE INTO pages (original_url, timestamp) VALUES (?, ?)', (norm_url, timestamp))
                if cursor.rowcount > 0:
                    count += 1
            except Exception as e:
                print(f"Error inserting {norm_url}: {e}")

    conn.commit()
    print(f"Discovered and added {count} new potential blog post URLs.")
    conn.close()

def extract_content(html):
    soup = BeautifulSoup(html, 'html.parser')
    
    # Try to find the blog post container. Blogspot often uses class 'post' or 'post-body'
    post = soup.find('div', class_='post') or soup.find('div', class_='post-outer')
    if not post:
        return None
    
    title_tag = post.find('h3', class_='post-title') or post.find('h2', class_='post-title')
    title = title_tag.get_text(strip=True) if title_tag else "Untitled Post"
    
    date_tag = soup.find('h2', class_='date-header')
    date = date_tag.get_text(strip=True) if date_tag else "Unknown Date"
    
    content_div = post.find('div', class_='post-body') or post
    # Remove script and style tags from content
    for s in content_div(['script', 'style']):
        s.decompose()
        
    # Convert to markdown
    content_html = str(content_div)
    content_md = md(content_html, heading_style="ATX")
    
    return {
        'title': title,
        'date': date,
        'content': content_md
    }

def get_session():
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def process_single_url(row, session):
    row_id, original_url, timestamp = row
    archived_url = f"https://web.archive.org/web/{timestamp}/{original_url}"
    print(f"Fetching {archived_url}...")
    
    try:
        response = session.get(archived_url, timeout=30)
        if response.status_code == 200:
            data = extract_content(response.text)
            if data:
                # Parse date to ISO format for sorting
                # Example: "Tuesday, December 23, 2008"
                date_str = data['date']
                iso_date = date_str
                try:
                    # Strip any leading/trailing whitespace
                    dt = datetime.strptime(date_str.strip(), "%A, %B %d, %Y")
                    iso_date = dt.strftime("%Y-%m-%d")
                except Exception as e:
                    # Fallback: clean the original string a bit
                    iso_date = re.sub(r'[^\w\s-]', '', date_str).replace(" ", "_")

                # Clean title for filename
                safe_title = re.sub(r'[^\w\s-]', '', data['title']).strip().lower()
                safe_title = re.sub(r'[-\s]+', '-', safe_title)
                filename = f"{iso_date}-{safe_title}.md"
                file_path = os.path.join(POSTS_DIR, filename)
                
                # Prepare frontmatter
                meta = {
                    'title': data['title'],
                    'date': data['date'],
                    'original_url': original_url,
                    'archived_url': archived_url
                }
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write("---\n")
                    yaml.dump(meta, f)
                    f.write("---\n\n")
                    f.write(data['content'])
                
                return {
                    'id': row_id,
                    'status': 'SUCCESS',
                    'archived_url': archived_url,
                    'http_status': response.status_code,
                    'local_path': file_path
                }
            else:
                return {
                    'id': row_id,
                    'status': 'FAILED',
                    'error_message': 'Failed to extract content',
                    'http_status': response.status_code
                }
        else:
            return {
                'id': row_id,
                'status': 'FAILED',
                'error_message': f'HTTP {response.status_code}',
                'http_status': response.status_code
            }
                
    except Exception as e:
        return {
            'id': row_id,
            'status': 'FAILED',
            'error_message': str(e)
        }
    finally:
        time.sleep(2) # Be nice to Wayback Machine

def process_urls(limit=None, max_workers=3):
    if not os.path.exists(POSTS_DIR):
        os.makedirs(POSTS_DIR)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    query = "SELECT id, original_url, timestamp FROM pages WHERE status IN ('PENDING', 'FAILED')"
    if limit:
        query += f" LIMIT {limit}"
    cursor.execute(query)
    rows = cursor.fetchall()
    
    print(f"Processing {len(rows)} URLs with {max_workers} workers...")
    session = get_session()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_row = {executor.submit(process_single_url, row, session): row for row in rows}
        
        for future in as_completed(future_to_row):
            result = future.result()
            row_id = result['id']
            if result['status'] == 'SUCCESS':
                cursor.execute("UPDATE pages SET status = 'SUCCESS', archived_url = ?, http_status = ?, local_path = ? WHERE id = ?", 
                               (result['archived_url'], result['http_status'], result['local_path'], row_id))
                print(f"Success: {result['local_path']}")
            else:
                cursor.execute("UPDATE pages SET status = 'FAILED', error_message = ?, http_status = ? WHERE id = ?", 
                               (result['error_message'], result.get('http_status'), row_id))
                print(f"Failed: row {row_id} - {result['error_message']}")
            
            conn.commit()

    conn.close()

if __name__ == "__main__":
    init_db()
    #discover_urls()
    process_urls(limit=None)
