import os
import json
import requests
import feedparser
import time
import re
import random
from datetime import datetime
from slugify import slugify
from io import BytesIO
from PIL import Image, ImageEnhance, ImageOps
from duckduckgo_search import DDGS 
from groq import Groq, APIError, RateLimitError, BadRequestError

# --- CONFIGURATION ---
GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "")
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: Groq API Key is missing!")
    exit(1)

# --- CATEGORY RSS FEED (ENGLISH / GLOBAL SOURCES) ---
# We use 'when:1d' to ensure the news is fresh (last 24 hours)
CATEGORY_URLS = {
    "Transfer News": "https://news.google.com/rss/search?q=football+transfer+news+Fabrizio+Romano+here+we+go+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Premier League": "https://news.google.com/rss/search?q=Premier+League+news+match+result+highlights+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Champions League": "https://news.google.com/rss/search?q=UEFA+Champions+League+news+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "La Liga": "https://news.google.com/rss/search?q=La+Liga+Real+Madrid+Barcelona+news+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "International Football": "https://news.google.com/rss/search?q=International+Football+news+FIFA+World+Cup+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Match Predictions": "https://news.google.com/rss/search?q=football+match+prediction+preview+predicted+lineup+when:1d&hl=en-GB&gl=GB&ceid=GB:en"
}

CONTENT_DIR = "content/articles"
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"
AUTHOR_NAME = "Soccer Daily Editorial"

TARGET_PER_CATEGORY = 1 

# --- MEMORY SYSTEM ---
def load_link_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_link_to_memory(keyword, slug):
    os.makedirs(DATA_DIR, exist_ok=True)
    memory = load_link_memory()
    clean_key = keyword.lower().strip()
    memory[clean_key] = f"/articles/{slug}"
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def get_internal_links_context():
    memory = load_link_memory()
    items = list(memory.items())
    if len(items) > 30:
        items = random.sample(items, 30)
    return json.dumps(dict(items))

# --- HYBRID IMAGE ENGINE ---
def generate_ai_image(prompt, filename):
    """
    Backup: Generate photorealistic image using Flux-Realism if DDG fails.
    """
    print(f"      🎨 DDG Blocked. Switching to AI Generation: {prompt}...")
    
    # Prompt engineering for realistic sports photography
    enhanced_prompt = f"Real photography, {prompt}, 8k sports photo, realistic lighting, soccer stadium background, highly detailed, dynamic angle, 4k texture, action shot"
    safe_prompt = enhanced_prompt.replace(" ", "%20")[:300]
    
    image_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1280&height=720&nologo=true&model=flux-realism"
    
    try:
        # Long timeout (90s) to prevent 'Read timed out' errors
        response = requests.get(image_url, timeout=90)
        if response.status_code == 200:
            img = Image.open(BytesIO(response.content))
            img = img.convert("RGB")
            
            output_path = f"{IMAGE_DIR}/{filename}"
            img.save(output_path, "JPEG", quality=90, optimize=True)
            return True
    except Exception as e:
        print(f"      ❌ AI Gen Failed: {e}")
    
    return False

def download_and_optimize_image(query, filename):
    """
    Try DDG first. If 403 Blocked -> Use AI.
    """
    search_query = f"{query} soccer match action wallpaper 4k"
    print(f"      🔍 Searching Image: {search_query}...")
    
    image_url = None
    
    # 1. TRY REAL IMAGE (DDG)
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(
                keywords=search_query, 
                region="wt-wt", 
                safesearch="off", 
                size="Wallpaper", 
                type_image="photo", 
                max_results=2
            ))
            if results:
                image_url = results[0]['image']
    except Exception as e:
        print(f"      ⚠️ Search Engine Error/Blocked: {e}")
    
    # 2. IF FAILED -> USE AI BACKUP
    if not image_url:
        print("      ⚠️ Real image not found/blocked. Using AI Backup...")
        return generate_ai_image(query, filename)

    # 3. IF FOUND -> PROCESS
    try:
        print(f"      ⬇️ Downloading Real Image: {image_url[:40]}...")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(image_url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            img = Image.open(BytesIO(response.content))
            img = img.convert("RGB")
            
            # --- CROP ---
            width, height = img.size
            img = img.crop((width*0.1, height*0.1, width*0.9, height*0.85)) 
            
            # --- RESIZE & MIRROR ---
            img = img.resize((1280, 720), Image.Resampling.LANCZOS)
            img = ImageOps.mirror(img) 
            
            # --- ENHANCE ---
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.4)
            
            output_path = f"{IMAGE_DIR}/{filename}"
            img.save(output_path, "JPEG", quality=90, optimize=True)
            return True
            
    except Exception as e:
        print(f"      ⚠️ Process Fail: {e}")
        return generate_ai_image(query, filename)
    
    return False

# --- AI WRITER ENGINE (ENGLISH) ---
def parse_ai_response(text):
    try:
        parts = text.split("|||BODY_START|||")
        if len(parts) < 2: return None
        json_part = parts[0].strip()
        body_part = parts[1].strip()
        json_part = re.sub(r'```json\s*', '', json_part)
        json_part = re.sub(r'```', '', json_part)
        data = json.loads(json_part)
        data['content'] = body_part
        return data
    except Exception as e:
        print(f"      ❌ Parse Error: {e}")
        return None

def get_groq_article_seo(title, summary, link, internal_links_map, target_category):
    MODEL_NAME = "llama-3.3-70b-versatile"
    
    # --- PROMPT IN ENGLISH ---
    system_prompt = f"""
    You are a Senior Football Pundit and Journalist for 'Soccer Daily'.
    TARGET CATEGORY: {target_category}
    
    TASK: Write a high-quality, engaging football news article (800-1000 words) in ENGLISH.
    
    OUTPUT FORMAT (JSON REQUIRED):
    {{"title": "Catchy Headline (Max 70 chars)", "description": "SEO Summary (Max 150 chars)", "category": "{target_category}", "main_keyword": "Main Player/Team Name"}}
    |||BODY_START|||
    [Markdown Content]

    STYLE GUIDE:
    1. **Tone**: Professional, passionate, and authoritative (British English preferred for soccer terms).
    2. **Structure**:
       - **Key Highlights** (Bullet points at top).
       - **Introduction**: The Hook & 5W1H (Who, What, Where, When, Why, How).
       - **Tactical Analysis / Context**: Deep dive into the match/news.
       - **Stats / Data**: Include relevant stats if available.
       - **Quotes**: Mention reaction from managers/players (simulated based on context).
       - **Verdict / Outlook**: What happens next?
    3. **SEO**: Use internal links from this list: {internal_links_map} -> Syntax: [Keyword](/articles/slug).
    4. **Originality**: Do not just copy the summary. Expand with expert analysis.
    """

    user_prompt = f"""
    Source News: {title}
    Summary: {summary}
    Original Link: {link}
    
    Write the article now.
    """

    for index, api_key in enumerate(GROQ_API_KEYS):
        try:
            print(f"      🤖 AI Writing ({target_category})...")
            client = Groq(api_key=api_key)
            completion = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=6000,
            )
            return completion.choices[0].message.content
        except Exception as e:
            print(f"      ⚠️ Error (Key #{index+1}): {e}")
            continue
            
    return None

# --- MAIN LOOP ---
def main():
    os.makedirs(CONTENT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    total_generated = 0

    for category_name, rss_url in CATEGORY_URLS.items():
        print(f"\n📡 Fetching Category: {category_name}...")
        try:
            feed = feedparser.parse(rss_url)
        except Exception as e:
            print(f"   ⚠️ Error fetching RSS: {e}")
            continue
        
        if not feed.entries:
            print(f"   ⚠️ Empty/Skip.")
            continue

        cat_success_count = 0
        
        for entry in feed.entries:
            if cat_success_count >= TARGET_PER_CATEGORY:
                break

            clean_title = entry.title.split(" - ")[0]
            slug = slugify(clean_title)
            filename = f"{slug}.md"

            if os.path.exists(f"{CONTENT_DIR}/{filename}"):
                continue

            print(f"   🔥 Processing: {clean_title[:50]}...")
            
            # 1. AI Text
            context = get_internal_links_context()
            raw_response = get_groq_article_seo(clean_title, entry.summary, entry.link, context, category_name)
            
            if not raw_response: continue

            data = parse_ai_response(raw_response)
            if not data: continue

            # 2. Image (Hybrid: Try Real -> Fail -> Use AI)
            img_name = f"{slug}.jpg"
            has_img = download_and_optimize_image(data['main_keyword'], img_name)
            
            final_img = f"/images/{img_name}" if has_img else "/images/default-football.jpg"
            
            # 3. Save
            date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00") # UTC Time
            
            md = f"""---
title: "{data['title'].replace('"', "'")}"
date: {date}
author: "{AUTHOR_NAME}"
categories: ["{data['category']}"]
tags: ["{data['main_keyword']}", "Soccer News", "Football"]
featured_image: "{final_img}"
description: "{data['description'].replace('"', "'")}"
draft: false
---

{data['content']}

---
*Source: Analysis by Soccer Daily based on international reports and [Original Story]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f: f.write(md)
            
            if 'main_keyword' in data: 
                save_link_to_memory(data['main_keyword'], slug)
            
            print(f"   ✅ Published: {filename}")
            cat_success_count += 1
            total_generated += 1
            
            print("   zzz... Cooling down 10s...")
            time.sleep(10)

    print(f"\n🎉 DONE! Total generated: {total_generated}")

if __name__ == "__main__":
    main()
