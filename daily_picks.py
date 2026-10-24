import os
import requests
import time
from datetime import datetime

FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# This is the line that was missing!
LEAGUES_TO_SCAN = ["turkey2", "germany2", "premier"]

def get_fixtures(league_key):
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    # Simple retry loop (3 attempts)
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=60)
            r.raise_for_status()
            return r.json().get("matches", [])
        except Exception as e:
            print(f"Attempt {attempt+1} failed for {league_key}: {e}")
            time.sleep(5) 
            
    print(f"Failed to fetch {league_key} after 3 attempts.")
    return []

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
        
        if ht_under >= 60:
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
        "parse_mode": "Markdown"
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
    
    for league in LEAGUES_TO_SCAN:
        print(f"Scanning {league}...")
        matches = get_fixtures(league)
        picks = filter_picks(matches)
        for p in picks:
            p["league"] = league
        all_picks.extend(picks)
    
    all_picks.sort(key=lambda x: x["prob"], reverse=True)
    
    if all_picks:
        message = f"🎯 *Daily AI Picks - {datetime.now().strftime('%Y-%m-%d')}*\n\n"
        for p in all_picks[:10]:
            message += f"⚽ {p['match']} ({p['league']})\n"
            message += f"⏰ {p['time']} | HT Under 1.5: *{p['prob']}%*\n\n"
        
        send_telegram_message(message)
    else:
        send_telegram_message("⚠️ No matches met the criteria today.")
