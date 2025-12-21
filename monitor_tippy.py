#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MONITOR BLOG TIPPY - VERSIONE COMPLETA
1. Monitora feed RSS per nuovi post con data nel titolo
2. Scarica le immagini originali
3. Rimuove duplicati e crea file CBZ
"""

import feedparser, json, os, sys, requests, zipfile, io, re, time, dropbox
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# CONFIGURAZIONE
FEED_URL = "https://tippylahostess.blogspot.com/feeds/posts/default?alt=rss"
STATE_FILE = "tippy_state.json"
LOG_FILE = "tippy_monitor.log"
DOWNLOAD_PATH = Path("D:/Fumetti")
TEMP_PATH = DOWNLOAD_PATH / "temp"
FINAL_PATH = DOWNLOAD_PATH / "final"
MAX_WORKERS = 5
TIMEOUT = 15
SKIP_IMAGES = 5  # Numero di immagini iniziali da saltare (banner/avatar)
THRESHOLD_RATIO = 0.85
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')

def upload_to_dropbox(file_path, dropbox_path):
    # Recupera il token dalle variabili d'ambiente (per sicurezza su GitHub)
    dbx_token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    if not dbx_token:
        log_message("❌ Token Dropbox non trovato nelle variabili d'ambiente")
        return False

    try:
        with dropbox.Dropbox(dbx_token) as dbx:
            with open(file_path, "rb") as f:
                dbx.files_upload(f.read(), dropbox_path, mode=dropbox.files.WriteMode.overwrite)
        log_message(f"✅ Caricato su Dropbox: {dropbox_path}")
        return True
    except Exception as e:
        log_message(f"❌ Errore caricamento Dropbox: {e}")
        return False
def log_message(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {msg}"
    print(log_entry)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry + "\n")
    except:
        pass

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("seen_posts", [])), data.get("last_check", None)
    except FileNotFoundError:
        return set(), None

def save_state(seen_posts):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "seen_posts": list(seen_posts), 
                "last_check": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_message(f"ERRORE salvataggio stato: {e}")

def fetch_feed():
    try:
        log_message(">>> Scaricamento feed RSS...")
        feed = feedparser.parse(FEED_URL)
        return feed
    except Exception as e:
        log_message(f"❌ ERRORE nel download del feed: {e}")
        return None

def sanitize_filename(filename):
    sanitized = re.sub(r'[<>:"/\|?*]', '', filename)
    return sanitized[:100].strip()

def fetch_page(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=TIMEOUT)
        return response.text
    except Exception as e:
        log_message(f"❌ ERRORE download pagina: {e}")
        return None

def extract_image_urls(html_content):
    pattern = r'https?://[^\s"]+(?:jpg|jpeg|png|gif|webp)'
    urls = re.findall(pattern, html_content, re.IGNORECASE)
    seen = set()
    unique_urls = []
    for url in urls:
        url_clean = url.split('?')[0]
        if url_clean not in seen:
            seen.add(url_clean)
            unique_urls.append(url_clean)
    
    # Saltiamo le immagini di intestazione del blog
    fumetto_urls = unique_urls[SKIP_IMAGES:]
    return fumetto_urls

def download_image(url, page_number, total):
    try:
        response = requests.get(url, timeout=TIMEOUT)
        if response.status_code == 200:
            filename = f"pag{page_number:03d}.jpg"
            return page_number, filename, response.content
    except:
        pass
    return None

def create_cbz(image_urls, cbz_filename):
    TEMP_PATH.mkdir(parents=True, exist_ok=True)
    cbz_path = TEMP_PATH / f"{cbz_filename}.cbz"
    
    total = len(image_urls)
    downloaded_images = {}
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(download_image, url, i+1, total): i+1 for i, url in enumerate(image_urls)}
        for future in as_completed(futures):
            result = future.result()
            if result:
                page_number, filename, content = result
                downloaded_images[page_number] = (filename, content)

    if not downloaded_images:
        return None

    with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_DEFLATED) as cbz:
        for page_number in sorted(downloaded_images.keys()):
            filename, content = downloaded_images[page_number]
            cbz.writestr(filename, content)
    
    return cbz_path

def remove_duplicate_pages(cbz_path):
    """Mantiene la logica di deduplicazione basata sulla dimensione dell'area immagine"""
    try:
        with zipfile.ZipFile(cbz_path, 'r') as zin:
            names = sorted(zin.namelist())
            img_names = [n for n in names if n.lower().endswith(IMAGE_EXTS)]
            if not img_names: return cbz_path

            # Identifica area di riferimento (media delle immagini centrali)
            ref_area = 0
            sample_indices = range(min(5, len(img_names)), min(15, len(img_names)))
            for i in sample_indices:
                with zin.open(img_names[i]) as f:
                    with Image.open(f) as img:
                        w, h = img.size
                        ref_area = max(ref_area, w * h)
            
            FINAL_PATH.mkdir(parents=True, exist_ok=True)
            out_file = FINAL_PATH / cbz_path.name
            
            with zipfile.ZipFile(out_file, 'w', zipfile.ZIP_DEFLATED) as zout:
                for n in names:
                    data = zin.read(n)
                    if n in img_names:
                        with Image.open(io.BytesIO(data)) as img:
                            w, h = img.size
                            if (w * h) >= (ref_area * THRESHOLD_RATIO):
                                zout.writestr(n, data)
                    else:
                        zout.writestr(n, data)
            return out_file
    except Exception as e:
        log_message(f"ERRORE deduplicazione: {e}")
        return cbz_path

def download_fumetto(post_url, post_title):
    log_message(f"--- Inizio download: {post_title}")
    html_content = fetch_page(post_url)
    if not html_content: return False
    
    image_urls = extract_image_urls(html_content)
    if not image_urls:
        log_message("Nessuna immagine trovata nel post.")
        return False
    
    cbz_name = sanitize_filename(post_title)
    cbz_temp = create_cbz(image_urls, cbz_name)
    
    if cbz_temp:
        # Sposta e pulisce duplicati
        final_file = remove_duplicate_pages(cbz_temp)
       if cbz_temp:
        final_file = remove_duplicate_pages(cbz_temp)
        
        # NUOVA LOGICA: Caricamento su Dropbox
        dbx_destination = f"/Fumetti_Tippy/{final_file.name}"
        upload_to_dropbox(final_file, dbx_destination)
        
        if cbz_temp.exists() and cbz_temp != final_file:
            cbz_temp.unlink()
        return True
    return False

def check_new_posts():
    log_message("="*50)
    log_message("AVVIO MONITOR BLOG TIPPY")
    log_message("="*50)
    
    seen_posts, _ = load_state()
    feed = fetch_feed()
    
    if not feed or not feed.entries:
        log_message("Feed vuoto o non raggiungibile.")
        return

    new_count = 0
    # reversed per processare dal più vecchio al più recente
    for entry in reversed(feed.entries):
        post_id = entry.get("id", entry.get("link", ""))
        title = entry.get("title", "")

        # VERIFICA DATA NEL TITOLO (es. 21/12/2025)
        if not re.search(r'\d{2}/\d{2}/\d{4}', title):
            continue

        if post_id not in seen_posts:
            log_message(f"Nuovo contenuto trovato: {title}")
            if download_fumetto(entry.link, title):
                seen_posts.add(post_id)
                save_state(seen_posts)
                new_count += 1
                time.sleep(2) # Pausa di cortesia tra i post
    
    log_message(f"Fine sessione. Nuovi fumetti: {new_count}")

if __name__ == "__main__":
    check_new_posts()
