import os
import re
import hashlib

POSTS_DIR = "src/content/posts"

def find_original_url(target_hash):
    files = [os.path.join(POSTS_DIR, f) for f in os.listdir(POSTS_DIR) if f.endswith('.md')]
    
    # Since I don't have the original URL map, I'll search for the hash in my logic history
    # OR, I can re-scrape the archive URL of the post and MD5 the candidates.
    
    # Let's try searching the post content first to see if I left any clues.
    # Actually, the image_downloader.py didn't store the map. 
    # I'll just look at the post again and try to find the remote URLs.
    pass

def scan_posts_for_risky_hosts():
    # Known risky hosts that often get hijacked or recycle links
    risky_hosts = ['tinypic.com', 'imageshack.us', 'photobucket.com']
    
    files = [os.path.join(POSTS_DIR, f) for f in os.listdir(POSTS_DIR) if f.endswith('.md')]
    for f_path in files:
        with open(f_path, 'r') as f:
            content = f.read()
            if any(host in content for host in risky_hosts):
                print(f"Risky host found in {f_path}")
                # Print lines containing archive.org links to these hosts
                lines = content.split('\n')
                for line in lines:
                    if any(host in line for host in risky_hosts):
                        print(f"  {line}")

if __name__ == "__main__":
    scan_posts_for_risky_hosts()
