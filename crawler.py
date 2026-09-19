import os
import sys
import time
import json
import re
import argparse
import uuid
import requests
from datetime import datetime, timezone
from collections import deque
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
IG_SESSION_ID = os.environ.get("IG_SESSION_ID")

if SUPABASE_URL and SUPABASE_URL.endswith("/rest/v1/"):
    SUPABASE_URL = SUPABASE_URL[:-9]
elif SUPABASE_URL and SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[:-8]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

IG_HEADERS = {
    "User-Agent": "Instagram 278.0.0.19.115 (iPhone14,2; iOS 16_5; en_US; scale=3.00; 1170x2532)",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": f"sessionid={IG_SESSION_ID}" if IG_SESSION_ID else ""
}

def clean_username(u):
    if not u:
        return ""
    u = str(u).strip()
    if "instagram.com/" in u:
        u = u.split("instagram.com/")[-1].split("?")[0].split("/")[0]
    return u.lower().replace("@", "").strip()


def get_existing_handles():
    """
    Collects all handles already in the Creator table or CreatorSuggestion table.
    """
    existing = set()
    
    # Existing creators
    try:
        res_creators = supabase.table("CreatorPlatform").select("handle").execute()
        for r in (res_creators.data or []):
            h = clean_username(r.get("handle"))
            if h:
                existing.add(h)
    except Exception as e:
        print(f"Error fetching existing creator handles: {e}")

    # Existing suggestions
    try:
        res_sugg = supabase.table("CreatorSuggestion").select("username").execute()
        for r in (res_sugg.data or []):
            u = clean_username(r.get("username"))
            if u:
                existing.add(u)
    except Exception as e:
        print(f"Error fetching existing suggestions: {e}")

    return existing

def get_crawled_seeds():
    """
    Returns set of handles that have already been used as crawl seeds in CrawledSeed.
    """
    crawled = set()
    try:
        res = supabase.table("CrawledSeed").select("username").execute()
        for r in (res.data or []):
            u = clean_username(r.get("username"))
            if u:
                crawled.add(u)
    except Exception as e:
        print(f"Error reading CrawledSeed table: {e}")
    return crawled

def mark_as_crawled(username, is_seed=True):
    """
    Records a username into CrawledSeed to prevent redundant crawling.
    """
    u = clean_username(username)
    if not u:
        return
    try:
        record = {
            "id": str(uuid.uuid4()),
            "username": u,
            "platform": "INSTAGRAM",
            "isSeed": is_seed,
            "crawledAt": datetime.now(timezone.utc).isoformat()
        }
        supabase.table("CrawledSeed").upsert(record, on_conflict="username").execute()
    except Exception as e:
        print(f"  [!] Failed to mark @{u} in CrawledSeed: {e}")

def get_uncrawled_db_seeds(limit=5):
    """
    Finds verified or high-priority Instagram handles from CreatorPlatform
    that have NOT yet been recorded in CrawledSeed.
    """
    crawled = get_crawled_seeds()
    seeds = []
    try:
        res = supabase.table("CreatorPlatform").select("handle, verified, followers").eq("platform", "INSTAGRAM").order("followers", desc=True).limit(50).execute()
        for r in (res.data or []):
            h = clean_username(r.get("handle"))
            if h and h not in crawled and h not in seeds:
                seeds.append(h)
                if len(seeds) >= limit:
                    break
    except Exception as e:
        print(f"Error fetching uncrawled seeds from DB: {e}")
    return seeds

def get_instagram_user_pk(handle):
    """
    Extracts the numeric user ID (pk) for an Instagram username from public HTML.
    """
    clean_handle = clean_username(handle)
    try:
        resp = requests.get(
            f"https://www.instagram.com/{clean_handle}/",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)"},
            timeout=15
        )
        if resp.status_code == 200:
            m = re.search(r'"id":"(\d+)"', resp.text)
            if m:
                return m.group(1)
    except Exception as e:
        print(f"Error getting pk for @{clean_handle}: {e}")
    return None

def fetch_suggested_creators(target_pk):
    """
    Queries Instagram's official discover/chaining API for a given creator ID.
    Returns list of suggested user dictionaries.
    """
    if not IG_SESSION_ID:
        print("ERROR: IG_SESSION_ID is required in .env for discover/chaining crawler.")
        return []

    url = f"https://i.instagram.com/api/v1/discover/chaining/?target_id={target_pk}"
    try:
        resp = requests.get(url, headers=IG_HEADERS, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("users", [])
        else:
            print(f"Chaining request failed with HTTP {resp.status_code}")
    except Exception as e:
        print(f"Chaining API exception: {e}")
    return []

def run_crawler(seed_handles=None, max_new_suggestions=30, deep_sidecar=False, db_seed_limit=5):
    """
    Crawls suggested creators.
    - Default (depth=1): Crawls specified or DB-fetched seeds, inserts to suggestions, marks in CrawledSeed, and finishes.
    - Deep Sidecar (--deep / --sidecar): Also enqueues returned creators to be crawled recursively in the same run,
      marking each crawled returnee in CrawledSeed as well.
    """
    print(f"[{time.strftime('%X')}] Starting Caskayd Discovery Crawler Bot...")
    mode_desc = "⚡ Deep Sidecar Mode (Recursive Tree)" if deep_sidecar else "🎯 Depth-1 Seed Mode (Curated)"
    print(f"[{time.strftime('%X')}] Running in: {mode_desc}")
    
    existing_handles = get_existing_handles()
    crawled_seeds = get_crawled_seeds()
    print(f"[{time.strftime('%X')}] Loaded {len(existing_handles)} existing handles & {len(crawled_seeds)} previously crawled seeds.")

    # Determine initial seeds
    initial_seeds = []
    if seed_handles:
        for s in seed_handles:
            cs = clean_username(s)
            if cs:
                initial_seeds.append(cs)
    else:
        print(f"[{time.strftime('%X')}] Looking up uncrawled seeds from database...")
        initial_seeds = get_uncrawled_db_seeds(limit=db_seed_limit)
        if not initial_seeds:
            print(f"[{time.strftime('%X')}] No uncrawled seeds found in DB. Falling back to default top seeds.")
            initial_seeds = ["hildabaci", "brodashagi", "taaooma", "fisayofosudo"]

    print(f"[{time.strftime('%X')}] Initial seeds: {', '.join(['@' + s for s in initial_seeds])}")

    queue = deque(initial_seeds)
    new_discovered_count = 0

    while queue and new_discovered_count < max_new_suggestions:
        seed = queue.popleft()
        seed = clean_username(seed)

        if not seed:
            continue

        if seed in crawled_seeds:
            print(f"[{time.strftime('%X')}] Skipping @{seed} (already in CrawledSeed history).")
            continue

        print(f"\n[{time.strftime('%X')}] Exploring seed creator: @{seed}...")
        pk = get_instagram_user_pk(seed)
        if not pk:
            print(f"Could not resolve pk for @{seed}, skipping.")
            mark_as_crawled(seed, is_seed=True)
            crawled_seeds.add(seed)
            continue

        suggested_users = fetch_suggested_creators(pk)
        print(f"[{time.strftime('%X')}] Discovered {len(suggested_users)} chained accounts from @{seed}.")

        # Mark this seed as crawled
        mark_as_crawled(seed, is_seed=True)
        crawled_seeds.add(seed)

        for u in suggested_users:
            if new_discovered_count >= max_new_suggestions:
                break

            username = clean_username(u.get("username"))
            if not username or username in existing_handles:
                continue

            # Skip private accounts
            if u.get("is_private", False):
                continue

            name = u.get("full_name") or username
            is_verified = u.get("is_verified", False)
            
            # Prepare suggestion record
            suggestion = {
                "id": str(uuid.uuid4()),
                "name": name,
                "username": username,
                "platform": "INSTAGRAM",
                "link": f"https://www.instagram.com/{username}/",
                "status": "PENDING"
            }

            try:
                supabase.table("CreatorSuggestion").insert(suggestion).execute()
                existing_handles.add(username)
                new_discovered_count += 1
                badge = " [VERIFIED]" if is_verified else ""
                print(f"  [+] Ingested new suggestion: @{username} ({name}){badge}")

                # If deep sidecar is active, enqueue returned creator for further crawling in same run
                if deep_sidecar and username not in crawled_seeds:
                    queue.append(username)

            except Exception as insert_err:
                print(f"  [!] Failed to insert @{username}: {insert_err}")

        time.sleep(2.5) # Polite pacing between Instagram API requests

    print(f"\n[{time.strftime('%X')}] Crawler complete! Ingested {new_discovered_count} new creator suggestions.")
    print(f"[{time.strftime('%X')}] CrawledSeed table updated. Run again anytime to discover from next DB seeds.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Caskayd Creator Discovery Crawler Bot.")
    parser.add_argument("--seeds", nargs="+", help="Explicit seed Instagram handle(s) to start from")
    parser.add_argument("--from-db", action="store_true", help="Pull uncrawled seeds directly from the database (default when no seeds given)")
    parser.add_argument("--max", type=int, default=30, help="Maximum new suggestions to discover (default: 30)")
    parser.add_argument("--db-limit", type=int, default=3, help="Number of uncrawled seeds to fetch from DB (default: 3)")
    parser.add_argument("--deep", "--sidecar", dest="deep", action="store_true", help="Deep Sidecar mode: recursively crawl returnees in the same run and record them in CrawledSeed")
    args = parser.parse_args()

    run_crawler(
        seed_handles=args.seeds,
        max_new_suggestions=args.max,
        deep_sidecar=args.deep,
        db_seed_limit=args.db_limit
    )
