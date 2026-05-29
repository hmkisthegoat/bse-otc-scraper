# BSE OTC Scraper

Fetches BSE Corporate Bond OTC trade data daily at 5:30 PM IST and pushes competitor rows to Supabase.

## Setup (5 minutes)

### 1. Create GitHub repo
- Go to github.com → New repository
- Name it `bse-otc-scraper` (private is fine)
- Upload both files: `scraper.py` and `.github/workflows/scrape.yml`

### 2. Add Supabase secrets
- Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions**
- Click **New repository secret** and add these two:

| Secret name    | Value                                      |
|----------------|--------------------------------------------|
| `SUPABASE_URL` | `https://gigbvkkwjjcmltoluiwd.supabase.co` |
| `SUPABASE_KEY` | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` |

### 3. Test it manually
- Go to your repo → **Actions** tab
- Click **BSE OTC Daily Scraper** → **Run workflow** → **Run workflow**
- Watch the logs — should show rows fetched and inserted

### 4. That's it
The scraper now runs automatically every weekday at 5:30 PM IST.
Your Netlify dashboard will show the new data the next time anyone opens it.

## Schedule
- Runs: Monday–Friday at 17:30 IST (12:00 UTC)
- Skips: Weekends automatically
- BSE holidays: No data will be found, scraper exits cleanly

## If BSE blocks the scraper
BSE occasionally changes their API. If the scraper stops working:
1. Go to repo → Actions → click the failed run → read the logs
2. The error will say what changed
3. Open an issue or update the `BSE_URL` in `scraper.py`
