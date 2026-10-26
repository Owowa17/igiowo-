import os
import requests
import time
from datetime import datetime, timezone

FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

LEAGUES_TO_SCAN = [
    "premier",      # England - Premier League
    "england2",     # England - Championship
    "spain1",       # Spain - La Liga
    "italy1",       # Italy - Serie A
    "italy2",       # Italy - Serie B
    "germany2",     # Germany - 2. Bundesliga
    "france2",      # France - Ligue 2
    "turkey2",      # Turkey - 1. Lig
    "brazil1",      # Brazil - Serie A
    "argentina1"    # Argentina - Primera Division
]
def get_fixtures(league_key):
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    for attempt in range(3):
        try:
            # Reduced timeout to 20s to prevent GH Action from hanging
            r = requests.get(url, headers=headers, timeout=20) 
            r.raise_for_status()
            data = r.json()
            matches = data.get("matches", [])
            
            # Freshness Check
            if matches:
                computed_at_str = matches[0].get("computed_at")
                if computed_at_str:
                    computed_date = computed_at_str.split("T")[0]
                    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    
                    if computed_date != today_utc:
                        print(f"⚠️ Data for {league_key} is stale (Computed: {computed_date}). API hasn't refreshed yet.")
                        return None # Return None to indicate stale data/failure
                        
            return matches # Returns [] if successful but no matches
        except Exception as e:
            print(f"Attempt {attempt+1} failed for {league_key}: {e}")
            time.sleep(5) 
            
    print(f"Failed to fetch {league_key} after 3 attempts.")
    return None # Return None on total failure

def filter_picks(matches):
    candidates = []
    for match in matches:
        model_preds = match.get("model_predictions") or {}
        dc_v2 = model_preds.get("dc_v2") or {}
        preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
        
        ht_over = preds.get("ht_over_1.5")
        if not ht_over: 
            continue
        
        ht_under = (1.0 - float(ht_over)) * 100
        
        if ht_under >= 70:
            candidates.append({
                "match": f"{match['home_team']} vs {match['away_team']}",
                "time": match.get("time", "N/A"),
                "prob": round(ht_under, 1)
            })
    
    candidates.sort(key=lambda x: x["prob"], reverse=True)
    return candidates

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML" # Changed to HTML for safer parsing
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response_data = response.json()
        
        if response_data.get("ok"):
            print("✅ Message successfully sent to Telegram!")
        else:
            print(f"❌ TELEGRAM API ERROR: {response_data}")
            
    except Exception as e:
        print(f"❌ NETWORK ERROR: {e}")

if __name__ == "__main__":
    print("🤖 Starting daily AI scan...")
    all_picks = []
    api_failed_or_stale = False
    
    for league in LEAGUES_TO_SCAN:
        print(f"Scanning {league}...")
        matches = get_fixtures(league)
        
        # If matches is None, the API failed or data was stale
        if matches is None:
            api_failed_or_stale = True
            continue
            
        picks = filter_picks(matches)
        for p in picks:
            p["league"] = league
        all_picks.extend(picks)
    
    all_picks.sort(key=lambda x: x["prob"], reverse=True)
    
    if all_picks:
        # Changed Markdown (*) to HTML (<b>) tags
        message = f"🎯 <b>Daily AI Picks - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}</b>\n\n"
        for p in all_picks[:3]:
            message += f"⚽ {p['match']} ({p['league']})\n"
            message += f"⏰ {p['time']} | HT Under 1.5: <b>{p['prob']}%</b>\n\n"
        
        send_telegram_message(message)
    else:
        if api_failed_or_stale:
            print("Skipping Telegram send. Data is not fresh yet or API failed.")
        else:
            send_telegram_message("⚠️ No matches met the 70% criteria today.")
