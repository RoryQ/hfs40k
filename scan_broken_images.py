import os
from PIL import Image
import json

IMAGES_DIR = "public/images"
COVERS_DIR = "public/images/covers"

def check_images(directory):
    results = []
    for filename in os.listdir(directory):
        if not filename.endswith('.webp'): continue
        path = os.path.join(directory, filename)
        try:
            with Image.open(path) as img:
                w, h = img.size
                size = os.path.getsize(path)
                # Flag suspiciously small or non-square-ish files
                aspect = w / h if h > 0 else 0
                if size < 5000 or w < 100 or h < 100 or aspect > 2.5 or aspect < 0.4:
                    results.append({
                        "filename": filename,
                        "path": path,
                        "width": w,
                        "height": h,
                        "size": size,
                        "aspect": aspect
                    })
        except:
            results.append({"filename": filename, "path": path, "status": "BROKEN"})
    return results

if __name__ == "__main__":
    print("Checking main images...")
    bad_main = check_images(IMAGES_DIR)
    print(f"Found {len(bad_main)} suspicious main images.")
    
    print("Checking covers...")
    bad_covers = check_images(COVERS_DIR)
    print(f"Found {len(bad_covers)} suspicious covers.")
    
    all_bad = bad_main + bad_covers
    with open("suspicious_images.json", "w") as f:
        json.dump(all_bad, f, indent=2)
    
    for img in all_bad:
        print(f"Suspicious: {img['path']} ({img.get('width')}x{img.get('height')}, {img.get('size')} bytes)")
