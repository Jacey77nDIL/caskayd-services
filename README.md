# Caskayd Services

An automated Python micro-services suite for Caskayd powering True HD avatar ingestion, avatar refreshes, and automated creator discovery crawling.

## Services Overview

### 1. New Creator HD Avatar Daemon (`main.py`)
- Automatically listens for newly registered creators who do **not** yet have an avatar (`profileImage IS NULL AND pfpError IS NULL`).
- Never redownloads or overwrites existing creator profile pictures.
- **4-Tier Waterfall Scraper**:
  1. Instagram True HD mobile uncompressed extraction.
  2. TikTok 1080x1080 True HD avatar extraction.
  3. Instaloader query.
  4. Web HTML metadata fallback.
- **HDfy Engine**: Upscales low-res profile pictures via Lanczos high-order filter and applies subtle unsharp masking for studio-grade clarity.
- Uploads directly to Supabase Storage (`profile-picture/{creatorId}.jpg`) and updates `Creator.profileImage`.

Run daemon:
```bash
python -u main.py
```

---

### 2. Explicit Profile Picture Updater (`update_pfps.py`)
Explicitly refreshes profile pictures for creators who **already have an image**. Only runs when explicitly triggered.

- Refresh a specific creator:
  ```bash
  python update_pfps.py --id <creator_uuid>
  ```
- Batch refresh existing creators:
  ```bash
  python update_pfps.py --all --limit 50
  ```

---

### 3. Automated Discovery Crawler Bot (`crawler.py`)
An intelligent graph-walking discovery crawler that automatically expands Caskayd's creator database.

#### Architecture & History Tracking (`CrawledSeed` Table)
To prevent redundant API queries, every seed explored is permanently recorded in Supabase's `CrawledSeed` table:
- `id` (UUID / text)
- `username` (Instagram handle, unique)
- `platform` ("INSTAGRAM")
- `isSeed` (boolean)
- `crawledAt` (timestamp with timezone)

#### Operational Modes:

1. **Default: Depth-1 Database Loop (Curated)**:
   - Automatically queries the database for verified creators who have **not yet been recorded in `CrawledSeed`**.
   - Crawls their immediate algorithmic clusters (70–80 candidates per seed).
   - Ingests new candidates into `CreatorSuggestion` with status `PENDING`.
   - Records the explored creators in `CrawledSeed`.
   - Stops cleanly at Depth 1 so that new seeds are vetted before branching further.
   ```bash
   # Automatically crawl the next 3 uncrawled creators from the database
   python crawler.py --from-db --db-limit 3 --max 50

   # Or simply run without arguments (defaults to database uncrawled seeds)
   python crawler.py --max 50
   ```

2. **Explicit Seed Lookups**:
   - Manually trigger exploration from one or more specific creator handles:
   ```bash
   python crawler.py --seeds hildabaci brodashagi taaooma --max 100
   ```

3. **Deep Sidecar Mode (`--deep` / `--sidecar`)**:
   - For recursive tree exploration: each returned creator discovered during the crawl is automatically added to the exploration queue in the **same run**.
   - Every returned creator that gets crawled is recorded in `CrawledSeed` (even before being approved into the main `Creator` table), preventing repeat lookups.
   ```bash
   # Deep tree crawl starting from an explicit seed
   python crawler.py --seeds hildabaci --deep --max 200

   # Deep tree crawl starting from uncrawled DB seeds
   python crawler.py --from-db --deep --max 300
   ```

---


## Environment Variables (`.env`)

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-supabase-service-role-key
IG_SESSION_ID=your-instagram-session-cookie
```

## Setup

```bash
source venv/bin/activate
pip install -r requirements.txt
```
