import json
import os
import re
import sqlite3
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import yaml
from openai import OpenAI

DB_NAME = "tracking.db"
POSTS_DIR = "posts"

# Configure OpenRouter
api_key = os.environ.get("OPENROUTER_API_KEY")
client = None
if not api_key:
    print("Error: OPENROUTER_API_KEY environment variable not set.")
else:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


def get_artist_album_regex(title):
    patterns = [
        r"^(.+?)\s?[:\-–—]\s?(.+?)(?:\s[\(\[].*)?$",  # Artist - Album (Optional Year/Format)
        r"^(.+?)\s?[:\-–—]\s?(.+?)$",                # Artist - Album (No year)
    ]

    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            artist = match.group(1).strip()
            album = match.group(2).strip()
            album = re.sub(r"\s?[\(\[].*$", "", album).strip()
            return {"artist": artist, "album": album}
    return None


def get_artist_album_llm(title, client):
    if not client:
        return {"artist": None, "album": None}

    prompt = f"""
    The following is a title from a music blog called 'holyfuckingshit40000'.
    The blog primarily posts underground and experimental albums.

    Extract the musical artist and album name from this title: "{title}"

    Return the result strictly as a valid JSON object with keys "artist" and "album".
    If it's not a specific album post (e.g. it's a site announcement), return null for both keys.

    Example input: "Scott Walker- Scott 2 (FLAC)"
    Example output: {{"artist": "Scott Walker", "album": "Scott 2"}}
    """
    try:
        response = client.chat.completions.create(
            model="qwen/qwen-2.5-72b-instruct",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=30,
        )
        text = response.choices[0].message.content.strip()
        return json.loads(text)
    except Exception as e:
        # print(f"LLM Error for title '{title}': {e}")
        return {"artist": None, "album": None}


def search_itunes(artist, album, session):
    if not artist or not album:
        return None
    query = f"{artist} {album}"
    url = f"https://itunes.apple.com/search?term={urllib.parse.quote(query)}&entity=album&limit=1"
    try:
        response = session.get(url, timeout=10)
        data = response.json()
        if data["resultCount"] > 0:
            return data["results"][0].get("collectionViewUrl")
    except Exception as e:
        pass
    return None


def get_odesli_links(itunes_url, session):
    if not itunes_url:
        return {}
    url = f"https://api.song.link/v1-alpha.1/links?url={urllib.parse.quote(itunes_url)}"
    try:
        response = session.get(url, timeout=10)
        data = response.json()
        links = {}
        platform_links = data.get("linksByPlatform", {})
        if "spotify" in platform_links:
            links["spotify"] = platform_links["spotify"].get("url")
        if "appleMusic" in platform_links:
            links["apple_music"] = platform_links["appleMusic"].get("url")
        if "youtube" in platform_links:
            links["youtube"] = platform_links["youtube"].get("url")
        return links
    except Exception as e:
        pass
    return {}


def generate_search_links(artist, album):
    if not artist or not album:
        return {}
    query = urllib.parse.quote(f"{artist} {album}")
    return {
        "spotify": f"https://open.spotify.com/search/{query}",
        "apple_music": f"https://music.apple.com/us/search?term={query}",
        "youtube": f"https://www.youtube.com/results?search_query={query}",
    }


def process_single_enrichment(row, session, client):
    row_id, title = row

    # 1. Regex Pass
    meta = get_artist_album_regex(title)
    method = "Regex"

    if not meta:
        # 2. LLM Fallback
        meta = get_artist_album_llm(title, client)
        method = "LLM"

    artist = meta.get("artist")
    album = meta.get("album")

    spotify_url = None
    apple_music_url = None
    youtube_url = None

    if artist and album:
        # 3. API Lookups
        itunes_url = search_itunes(artist, album, session)
        links = get_odesli_links(itunes_url, session)

        spotify_url = links.get("spotify")
        apple_music_url = links.get("apple_music")
        youtube_url = links.get("youtube")

        # 4. Fallback Search Links
        fallbacks = generate_search_links(artist, album)
        if not spotify_url:
            spotify_url = fallbacks.get("spotify")
        if not apple_music_url:
            apple_music_url = fallbacks.get("apple_music")
        if not youtube_url:
            youtube_url = fallbacks.get("youtube")

    return {
        "id": row_id,
        "artist": artist,
        "album": album,
        "spotify": spotify_url,
        "apple_music": apple_music_url,
        "youtube": youtube_url,
        "method": method,
    }


def enrich_posts(limit=None, max_workers=15):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Get successful posts that are PENDING enrichment
    # We need to get the title from the markdown files, but for speed, let's assume we can parse it from local_path filename or read them once
    query = "SELECT id, local_path FROM pages WHERE status = 'SUCCESS' AND enrich_status = 'PENDING'"
    if limit:
        query += f" LIMIT {limit}"
    cursor.execute(query)
    rows = cursor.fetchall()

    pending_tasks = []
    for row_id, local_path in rows:
        if os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                content = f.read()
                parts = content.split("---")
                if len(parts) >= 3:
                    try:
                        frontmatter = yaml.safe_load(parts[1])
                        title = frontmatter.get("title")
                        if title:
                            pending_tasks.append((row_id, title))
                    except:
                        pass

    print(f"Enriching {len(pending_tasks)} posts with {max_workers} workers...")

    session = requests.Session()
    # Add a simple retry strategy for requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_single_enrichment, task, session, client): task
            for task in pending_tasks
        }

        count = 0
        for future in as_completed(futures):
            try:
                result = future.result()
                cursor.execute(
                    """
                    UPDATE pages
                    SET artist = ?, album = ?, spotify_url = ?, apple_music_url = ?, youtube_url = ?, enrich_status = 'SUCCESS'
                    WHERE id = ?
                """,
                    (
                        result["artist"],
                        result["album"],
                        result["spotify"],
                        result["apple_music"],
                        result["youtube"],
                        result["id"],
                    ),
                )

                count += 1
                if count % 10 == 0:
                    conn.commit()
                    print(f"Processed {count}/{len(pending_tasks)} posts...")
            except Exception as e:
                print(f"Error in future: {e}")

    conn.commit()
    conn.close()


def inject_metadata_to_markdown():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT local_path, artist, album, spotify_url, apple_music_url, youtube_url FROM pages WHERE enrich_status = 'SUCCESS'"
    )
    rows = cursor.fetchall()

    print(f"Injecting metadata into {len(rows)} markdown files...")

    for local_path, artist, album, spotify, apple, youtube in rows:
        if not os.path.exists(local_path):
            continue

        try:
            with open(local_path, "r", encoding="utf-8") as f:
                content = f.read()

            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1])
                # Only update if artist/album are found
                if artist or album:
                    frontmatter["artist"] = artist
                    frontmatter["album"] = album
                    frontmatter["spotify_url"] = spotify
                    frontmatter["apple_music_url"] = apple
                    frontmatter["youtube_url"] = youtube

                    new_frontmatter = yaml.dump(frontmatter, sort_keys=False)
                    new_content = f"---\n{new_frontmatter}---\n{parts[2]}"

                    with open(local_path, "w", encoding="utf-8") as f_out:
                        f_out.write(new_content)
        except Exception as e:
            print(f"Error updating {local_path}: {e}")

    conn.close()


if __name__ == "__main__":
    # To run a test batch:
    # enrich_posts(limit=20, max_workers=5)
    # inject_metadata_to_markdown()

    # To run full:
    enrich_posts(limit=None, max_workers=15)
    inject_metadata_to_markdown()
