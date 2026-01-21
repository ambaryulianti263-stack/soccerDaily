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
from PIL import Image, ImageEnhance, ImageOps # Library pengolah gambar
from duckduckgo_search import DDGS # Library pencari gambar
from groq import Groq, APIError, RateLimitError, BadRequestError

# --- KONFIGURASI API ---
GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "")
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: API Key Groq Kosong!")
    exit(1)

# --- KONFIGURASI SUMBER BERITA BOLA (RSS GOOGLE NEWS) ---
# Menggunakan parameter 'when:1d' untuk berita 24 jam terakhir
CATEGORY_URLS = {
    "Berita Transfer": "https://news.google.com/rss/search?q=football+transfer+news+Romano+here+we+go+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Liga Inggris": "https://news.google.com/rss/search?q=Premier+League+news+match+result+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Liga Champions": "https://news.google.com/rss/search?q=UEFA+Champions+League+news+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "La Liga": "https://news.google.com/rss/search?q=La+Liga+Real+Madrid+Barcelona+news+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Timnas Indonesia": "https://news.google.com/rss/search?q=Timnas+Indonesia+PSSI+STY+when:1d&hl=id-ID&gl=ID&ceid=ID:id",
    "Prediksi Pertandingan": "https://news.google.com/rss/search?q=football+match+prediction+preview+lineup+when:1d&hl=en-GB&gl=GB&ceid=GB:en"
}

CONTENT_DIR = "content/articles"
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"
AUTHOR_NAME = "Soccer Daily Admin"

# Target artikel per kategori setiap kali bot jalan
TARGET_PER_CATEGORY = 1 

# --- SISTEM MEMORI (Agar tidak posting berita sama) ---
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

# --- ENGINE GAMBAR (CARI REAL IMAGE + MODIFIKASI ANTI-COPYRIGHT) ---
def download_and_optimize_image(query, filename):
    """
    Mencari gambar di DuckDuckGo, Download ke RAM (tidak save disk),
    Crop Watermark, Mirror, Resize, lalu Save hasil akhirnya.
    """
    search_query = f"{query} soccer wallpaper 4k"
    print(f"      🔍 Mencari gambar: {search_query}...")
    
    # 1. Cari Gambar Resolusi Besar
    image_url = None
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(
                keywords=search_query, 
                region="wt-wt", 
                safesearch="off", 
                size="Wallpaper", # Wajib wallpaper agar tidak pecah saat dicrop
                type_image="photo", 
                max_results=3
            ))
            if results:
                image_url = results[0]['image']
    except Exception as e:
        print(f"      ⚠️ Search Error: {e}")
        return False

    if not image_url:
        print("      ❌ Gambar tidak ditemukan.")
        return False

    # 2. Download ke Memori & Manipulasi
    try:
        print(f"      ⬇️ Processing: {image_url[:40]}...")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(image_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            # Buka gambar di RAM (BytesIO) -> File asli TIDAK disimpan di komputer
            img = Image.open(BytesIO(response.content))
            img = img.convert("RGB") 
            
            # --- TAHAP 1: CROP WATERMARK (SMART CROP) ---
            width, height = img.size
            
            # Potong 15% (Kiri, Atas, Kanan)
            # Potong 20% (Bawah) -> Biasanya ticker berita/watermark ada di bawah
            left = width * 0.15
            top = height * 0.15
            right = width * 0.85
            bottom = height * 0.80
            
            img = img.crop((left, top, right, bottom))
            
            # --- TAHAP 2: RESIZE & MIRROR ---
            # Resize balik ke HD (1280x720) pakai Lanczos biar tajam
            img = img.resize((1280, 720), Image.Resampling.LANCZOS)
            
            # Mirroring (Balik Horizontal) -> Kunci lolos copyright
            img = ImageOps.mirror(img)
            
            # --- TAHAP 3: PERBAIKAN KUALITAS ---
            # Karena dicrop & di-zoom, kita pertajam (Sharpen)
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.5) # Tajamkan 50%
            
            # Ubah warna dikit biar histogram beda
            enhancer_col = ImageEnhance.Color(img)
            img = enhancer_col.enhance(1.1)

            # --- TAHAP 4: SIMPAN HASIL MODIFIKASI ---
            output_path = f"{IMAGE_DIR}/{filename}"
            # Saat save 'JPEG', metadata EXIF asli otomatis terbuang
            img.save(output_path, "JPEG", quality=90, optimize=True)
            
            return True
            
    except Exception as e:
        print(f"      ⚠️ Gagal memproses gambar: {e}")
    
    return False

# --- ENGINE AI (PENULIS BERITA BOLA) ---
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
    Anda adalah Analis Sepak Bola Senior & Pundit untuk 'Soccer Daily'.
    KATEGORI: {target_category}
    
    TUGAS: Tulis artikel berita/analisis sepak bola (800-1000 kata) dalam BAHASA INDONESIA.
    
    OUTPUT FORMAT (JSON WAJIB):
    {{"title": "Judul Clickbait Berkelas (Max 70 chars)", "description": "Ringkasan SEO (Max 150 chars)", "category": "{target_category}", "main_keyword": "Nama Pemain/Tim Utama"}}
    |||BODY_START|||
    [Isi Artikel Format Markdown]

    PANDUAN PENULISAN:
    1. **Gaya Bahasa**: Gunakan istilah bola (Brace, Blunder, Tiki-taka, High Pressing, Parkir Bus). Nada bicara seru tapi profesional.
    2. **Struktur**:
       - Intro (5W1H) yang menarik.
       - Analisis Taktik/Situasi.
       - Statistik (jika ada).
       - Kutipan/Reaksi Fans.
       - Kesimpulan/Prediksi.
    3. **SEO**: Masukkan internal link dari: {internal_links_map} -> Syntax: [Keyword](/articles/slug).
    4. Jangan menyalin mentah-mentah, buat narasi unik.
    """

    user_prompt = f"""
    Sumber Berita: {title}
    Ringkasan: {summary}
    Link Asli: {link}
    
    Buat artikel sekarang.
    """

    for index, api_key in enumerate(GROQ_API_KEYS):
        try:
            print(f"      🤖 AI Menulis ({target_category})...")
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
        try:
            feed = feedparser.parse(rss_url)
        except Exception as e:
            print(f"   ⚠️ Error fetching RSS: {e}")
            continue
        
        if not feed.entries:
            print(f"   ⚠️ Tidak ada berita baru untuk {category_name}.")
            continue

        cat_success_count = 0
        
        # LOOPING BERITA
        for entry in feed.entries:
            if cat_success_count >= TARGET_PER_CATEGORY:
                break

            clean_title = entry.title.split(" - ")[0]
            slug = slugify(clean_title)
            filename = f"{slug}.md"

            if os.path.exists(f"{CONTENT_DIR}/{filename}"):
                continue

            print(f"   🔥 Processing: {clean_title[:50]}...")
            
            # 1. Generate AI Text
            context = get_internal_links_context()
            raw_response = get_groq_article_seo(clean_title, entry.summary, entry.link, context, category_name)
            
            if not raw_response:
                print("      ❌ AI Failed.")
                continue

            data = parse_ai_response(raw_response)
            if not data:
                print("      ❌ Parse Failed.")
                continue

            # 2. Image Processing (Cari -> Download ke RAM -> Modif -> Save)
            img_name = f"{slug}.jpg"
            # Cari gambar berdasarkan keyword utama + 'action match'
            search_query = f"{data['main_keyword']} soccer match action"
            has_img = download_and_optimize_image(search_query, img_name)
            
            final_img = f"/images/{img_name}" if has_img else "/images/default-football.jpg"
            
            # 3. Save Markdown
            date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+07:00") # WIB
            
            md = f"""---
title: "{data['title'].replace('"', "'")}"
date: {date}
author: "{AUTHOR_NAME}"
categories: ["{data['category']}"]
tags: ["{data['main_keyword']}", "Berita Bola", "Soccer"]
featured_image: "{final_img}"
description: "{data['description'].replace('"', "'")}"
draft: false
---

{data['content']}

---
*Sumber: Analisis Soccer Daily berdasarkan laporan media internasional dan [Sumber Asli]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f: f.write(md)
            
            if 'main_keyword' in data: 
                save_link_to_memory(data['main_keyword'], slug)
            
            print(f"   ✅ Published: {filename}")
            cat_success_count += 1
            total_generated += 1
            
            print("   zzz... Istirahat 15 detik...")
            time.sleep(15)

    print(f"\n🎉 SELESAI! Total artikel dibuat: {total_generated}")

if __name__ == "__main__":
    main()
