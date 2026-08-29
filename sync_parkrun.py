import argparse
import json
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import requests

# --- ATHLETE CONFIGURATION ---
ATHLETES = [
    {"name": "Gary Manley", "id": "500760"},
    {"name": "Alf Oseni", "id": "995019"},
    {"name": "Katie Manley", "id": "350599"},
]

# Set this to your deployed worker URL:
PROXY_WORKER_URL = "https://parkrun-proxy.garymanley.workers.dev"

DEFAULT_DELAY = 1.5
DATA_DIR = Path("./data")
OUTPUT_FILE = DATA_DIR / "parkrun_data.json"
# -----------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def fetch_via_proxy(session: requests.Session, target_url: str) -> Optional[requests.Response]:
    """Wraps requests to route through the Cloudflare proxy."""
    encoded_url = urllib.parse.quote(target_url, safe="")
    proxy_url = f"{PROXY_WORKER_URL}?url={encoded_url}"
    return session.get(proxy_url, timeout=20)


def load_existing_dataset() -> Dict[str, Dict[str, Any]]:
    if not OUTPUT_FILE.exists():
        return {}
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            runs_list = json.load(f)
            return {f"{r.get('athleteId')}_{r.get('event')}_{r.get('runNumber')}": r for r in runs_list}
    except Exception as exc:
        logger.warning("Could not read %s: %s", OUTPUT_FILE, exc)
        return {}


def fetch_athlete_summary_runs(
    session: requests.Session, athlete_id: str, athlete_name: str
) -> List[Dict[str, Any]]:
    url = f"https://www.parkrun.org.uk/parkrunner/{athlete_id}/all/"
    logger.info("Fetching profile summary for %s (%s)...", athlete_name, athlete_id)

    response = fetch_via_proxy(session, url)
    if not response or response.status_code != 200:
        logger.error(
            "Failed to load profile for %s (HTTP %s)",
            athlete_name,
            response.status_code if response else "No Response",
        )
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    target_table = None
    for tbl in soup.find_all("table"):
        caption = tbl.find("caption")
        if caption and "all results" in caption.get_text(strip=True).lower():
            target_table = tbl
            break

    if not target_table:
        for tbl in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            if "event" in headers and "run date" in headers and "pos" in headers:
                target_table = tbl
                break

    if not target_table:
        logger.error("Could not locate 'All Results' table for %s", athlete_name)
        return []

    runs = []
    tbody = target_table.find("tbody")
    rows = tbody.find_all("tr") if tbody else target_table.find_all("tr")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        event_name = cells[0].get_text(strip=True)
        date_str = cells[1].get_text(strip=True)
        run_num_str = cells[2].get_text(strip=True)
        pos_str = cells[3].get_text(strip=True)
        time_str = cells[4].get_text(strip=True)
        age_grade = cells[5].get_text(strip=True)

        try:
            run_number = int(run_num_str)
            position = int(pos_str)
        except ValueError:
            continue

        run_link = cells[2].find("a") or cells[1].find("a")
        event_link = cells[0].find("a")

        if run_link and run_link.get("href"):
            event_url = run_link["href"]
        elif event_link and event_link.get("href"):
            base_url = event_link["href"].rstrip("/")
            event_url = f"{base_url}/{run_number}/"
        else:
            event_url = ""

        runs.append({
            "athleteId": athlete_id,
            "athleteName": athlete_name,
            "event": event_name,
            "eventUrl": event_url,
            "date": date_str,
            "runNumber": run_number,
            "position": position,
            "time": time_str,
            "ageGrade": age_grade,
            "enriched": None,
        })

    return runs


def enrich_event_result(
    session: requests.Session, run: Dict[str, Any], athlete_id: str
) -> Optional[Dict[str, Any]]:
    url = run.get("eventUrl")
    if not url:
        return None

    try:
        response = fetch_via_proxy(session, url)
        if not response or response.status_code != 200:
            logger.warning(
                "HTTP %s on %s",
                response.status_code if response else "No Response",
                url,
            )
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        result_rows = [
            r
            for r in soup.select(".Results-table-row, tr[data-name], tbody tr")
            if r.find("a", href=re.compile(r"/parkrunner/"))
        ]

        total_finishers = len(result_rows)
        user_row = next(
            (
                r
                for r in result_rows
                if r.find(
                    "a", href=re.compile(rf"/parkrunner/{athlete_id}(?:/|$)")
                )
            ),
            None,
        )

        if not user_row:
            return {"totalFinishers": total_finishers}

        # 1. Age group extraction
        age_group = user_row.get("data-agegroup") or ""
        if not age_group:
            age_cell = user_row.select_one(
                ".Results-table-td--ageGroup, td:nth-of-type(4)"
            )
            age_group = age_cell.get_text(strip=True) if age_cell else ""

        # 2. Gender extraction
        gender = user_row.get("data-gender") or ""
        if not gender:
            gender_cell = user_row.select_one(
                ".Results-table-td--gender, td:nth-of-type(3)"
            )
            gender = gender_cell.get_text(strip=True) if gender_cell else ""

        # 3. Category ranking calculation
        cat_rows = (
            [
                r
                for r in result_rows
                if (r.get("data-agegroup") == age_group)
                or (
                    r.select_one(
                        ".Results-table-td--ageGroup, td:nth-of-type(4)"
                    )
                    and r.select_one(
                        ".Results-table-td--ageGroup, td:nth-of-type(4)"
                    ).get_text(strip=True)
                    == age_group
                )
            ]
            if age_group
            else []
        )
        cat_pos = (
            cat_rows.index(user_row) + 1 if user_row in cat_rows else None
        )

        # 4. Gender ranking calculation
        gen_rows = (
            [
                r
                for r in result_rows
                if (r.get("data-gender") == gender)
                or (
                    r.select_one(".Results-table-td--gender, td:nth-of-type(3)")
                    and r.select_one(
                        ".Results-table-td--gender, td:nth-of-type(3)"
                    ).get_text(strip=True)
                    == gender
                )
            ]
            if gender
            else []
        )
        gen_pos = (
            gen_rows.index(user_row) + 1 if user_row in gen_rows else None
        )

        position = run.get("position", 0)
        finish_pct = (
            round((1 - (position / total_finishers)) * 100, 1)
            if total_finishers > 0
            else None
        )

        return {
            "totalFinishers": total_finishers,
            "finishPercentile": finish_pct,
            "ageCategory": age_group,
            "categoryPosition": cat_pos,
            "categoryTotal": len(cat_rows),
            "gender": gender,
            "genderPosition": gen_pos,
            "genderTotal": len(gen_rows),
        }

    except Exception as exc:
        logger.error("Error enriching %s: %s", url, exc)
        return None


def sync_all_athletes(batch_enrich_limit: Optional[int] = None, delay: float = DEFAULT_DELAY) -> None:
    session = requests.Session()
    master_store = load_existing_dataset()
    logger.info("Loaded %d existing run records from store.", len(master_store))

    total_enriched_this_run = 0

    for athlete in ATHLETES:
        clean_id = re.sub(r"^[Aa]", "", str(athlete["id"]).strip())
        name = athlete["name"]

        runs = fetch_athlete_summary_runs(session, clean_id, name)
        time.sleep(delay)

        for run in runs:
            key = f"{clean_id}_{run['event']}_{run['runNumber']}"

            if key in master_store and master_store[key].get("enriched"):
                continue

            if batch_enrich_limit is not None and total_enriched_this_run >= batch_enrich_limit:
                if key not in master_store:
                    master_store[key] = run
                continue

            logger.info("Enriching [%s] %s #%s...", name, run["event"], run["runNumber"])
            run["enriched"] = enrich_event_result(session, run, clean_id)
            master_store[key] = run
            total_enriched_this_run += 1
            time.sleep(delay)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    all_runs_list = list(master_store.values())

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_runs_list, f, indent=2, ensure_ascii=False)

    unenriched_count = sum(1 for r in all_runs_list if not r.get("enriched"))
    logger.info(
        "Sync Complete! Total stored: %d | Newly enriched: %d | Remaining unenriched: %d",
        len(all_runs_list),
        total_enriched_this_run,
        unenriched_count,
    )


def main():
    parser = argparse.ArgumentParser(description="Multi-Athlete parkrun Sync Pipeline")
    parser.add_argument("--batch-limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)

    args = parser.parse_args()
    sync_all_athletes(batch_enrich_limit=args.batch_limit, delay=args.delay)


if __name__ == "__main__":
    main()