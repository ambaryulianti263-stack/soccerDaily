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
from PIL import Image
from groq import Groq, APIError, RateLimitError, BadRequestError

# --- CONFIGURATION ---
GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "")
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: API Key Groq Kosong!")
    exit(1)

# DAFTAR KATEGORI & RSS URL GOOGLE NEWS (US REGION)
CATEGORY_URLS = {
    "US Politics": "https://news.google.com/rss/headlines/section/topic/POLITICS?hl=en-US&gl=US&ceid=US:en",
    "Business": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    "Technology": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
    "World": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "Science": "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
    "Health": "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=en-US&gl=US&ceid=US:en",
    "Entertainment": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en",
    "Sports": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en"
}

CONTENT_DIR = "content/articles"
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"
AUTHOR_NAME = "US News Desk"

# Target per kategori (Minimal 1)
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

# --- IMAGE ENGINE (ROBUST) ---
def download_and_optimize_image(prompt, filename):
    safe_prompt = prompt.replace(" ", "%20")[:200]
    # Menggunakan model Flux Realism
    image_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1280&height=720&nologo=true&model=flux-realism"
    
    for attempt in range(3):
        try:
            print(f"      🎨 Generating Image (Attempt {attempt+1})...")
            response = requests.get(image_url, timeout=60)
            if response.status_code == 200:
                img = Image.open(BytesIO(response.content))
                img = img.resize((1280, 720), Image.Resampling.LANCZOS)
                output_path = f"{IMAGE_DIR}/{filename}"
                img.convert("RGB").save(output_path, "JPEG", quality=80, optimize=True)
                return True
        except Exception as e:
            print(f"      ⚠️ Image fail: {e}")
            time.sleep(5)
    
    print("      ❌ Image failed after 3 attempts.")
    return False

# --- AI ENGINE ---
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
    
    system_prompt = f"""
    You are a Senior Journalist for 'US Daily'.
    TARGET CATEGORY: {target_category}
    
    TASK: Write a highly authoritative news article (1000+ words).
    
    OUTPUT FORMAT (STRICT):
    {{"title": "...", "description": "...", "category": "{target_category}", "main_keyword": "...", "image_prompt": "..."}}
    |||BODY_START|||
    [Markdown Article Content]

    METADATA RULES:
    - category: MUST BE EXACTLY '{target_category}'
    - description: SEO optimized, under 160 chars.
    - image_prompt: Photorealistic, cinematic, news style, 16:9 aspect ratio.

    ARTICLE STRUCTURE:
    1. **Key Takeaways** (Bulleted list)
    2. **Introduction** (5W1H)
    3. **Background & Context** (Deep dive)
    4. **Analysis** (Why it matters for US citizens)
    5. **Quotes & Reactions** (Simulated expert quotes)
    6. **Outlook** (What's next)
    
    STYLE:
    - Use H2 (##) and H3 (###).
    - Bold important entities.
    - Use internal links: {internal_links_map} -> Syntax: [Keyword](/articles/slug).
    - Objective, professional, CBS/NYT style.
    """

    user_prompt = f"""
    News: {title}
    Context: {summary}
    Link: {link}
    
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
                temperature=0.6,
                max_tokens=6500,
            )
            return completion.choices[0].message.content

        except BadRequestError as e:
            print(f"      ⚠️ GROQ 400 ERROR: {e.body}")
            continue
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

    # LOOPING SETIAP KATEGORI
    for category_name, rss_url in CATEGORY_URLS.items():
        print(f"\n📡 Fetching Category: {category_name}...")
        feed = feedparser.parse(rss_url)
        
        if not feed.entries:
            print(f"   ⚠️ No entries for {category_name}. Skipping.")
            continue

        cat_success_count = 0
        
        # LOOPING BERITA DI DALAM KATEGORI TERSEBUT
        for entry in feed.entries:
            # Jika sudah dapat 1 artikel untuk kategori ini, pindah ke kategori berikutnya
            if cat_success_count >= TARGET_PER_CATEGORY:
                break

            clean_title = entry.title.split(" - ")[0]
            slug = slugify(clean_title)
            filename = f"{slug}.md"

            if os.path.exists(f"{CONTENT_DIR}/{filename}"):
                # print(f"   ⏭️  Skipping (Exists): {clean_title[:20]}...")
                continue

            print(f"   🔥 Processing: {clean_title[:50]}...")
            
            # 1. Generate AI
            context = get_internal_links_context()
            # Kita kirim nama kategori spesifik ke AI agar akurat
            raw_response = get_groq_article_seo(clean_title, entry.summary, entry.link, context, category_name)
            
            if not raw_response:
                print("      ❌ AI Failed.")
                continue

            data = parse_ai_response(raw_response)
            if not data:
                print("      ❌ Parse Failed.")
                continue

            # 2. Image
            img_name = f"{slug}.jpg"
            has_img = download_and_optimize_image(data['image_prompt'], img_name)
            final_img = f"/images/{img_name}" if has_img else "/images/default-news.jpg"
            
            # 3. Save Markdown
            date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-05:00")
            
            md = f"""---
title: "{data['title'].replace('"', "'")}"
date: {date}
author: "{AUTHOR_NAME}"
categories: ["{data['category']}"]
tags: ["{data['main_keyword']}"]
featured_image: "{final_img}"
description: "{data['description'].replace('"', "'")}"
draft: false
---

{data['content']}

---
*Sources: Analysis based on reports from AP, Reuters, and [Original Story]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f: f.write(md)
            
            if 'main_keyword' in data: 
                save_link_to_memory(data['main_keyword'], slug)
            
            print(f"   ✅ Published: {filename}")
            cat_success_count += 1
            total_generated += 1
            
            # Jeda 15 detik agar API Groq & Pollinations aman
            print("   zzz... Cooling down 15s...")
            time.sleep(15)

    print(f"\n🎉 DONE! Total articles generated: {total_generated}")

if __name__ == "__main__":
    main()