import os
import requests
import time
from datetime import datetime, timezone

FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

LEAGUES_TO_SCAN = [
    "premier", "england2", "spain1", "italy1", "italy2", 
    "germany2", "france2", "turkey2", "brazil1", "argentina1"
]

def get_fixtures(league_key):
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=60)
            r.raise_for_status()
            data = r.json()
            matches = data.get("matches", [])
            
            # Freshness Check: Ensure the API has updated for today
            if matches:
                computed_at_str = matches[0].get("computed_at")
                if computed_at_str:
                    computed_date = computed_at_str.split("T")[0]
                    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if computed_date != today_utc:
                        print(f"⚠️ Data for {league_key} is stale.")
                        return [] 
            return matches
        except Exception as e:
            print(f"Attempt {attempt+1} failed for {league_key}: {e}")
            time.sleep(5) 
    return []

def filter_picks(matches):
    ht_candidates = []
    ft_candidates = []
    
    for match in matches:
        model_preds = match.get("model_predictions") or {}
        dc_v2 = model_preds.get("dc_v2") or {}
        preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
        
        # --- 1. HT Under 1.5 ---
        ht_over = preds.get("ht_over_1.5")
        if ht_over:
            ht_under = (1.0 - float(ht_over)) * 100
            if ht_under >= 70:
                ht_candidates.append({
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "time": match.get("time", "N/A"),
                    "prob": round(ht_under, 1),
                    "market": "HT Under 1.5"
                })
                
        # --- 2. FT Under 2.5 ---
        ft_over = preds.get("over_2.5")
        if ft_over:
            ft_under = (1.0 - float(ft_over)) * 100
            if ft_under >= 70: # Keeping the same 70% threshold
                ft_candidates.append({
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "time": match.get("time", "N/A"),
                    "prob": round(ft_under, 1),
                    "market": "FT Under 2.5"
                })
    
    ht_candidates.sort(key=lambda x: x["prob"], reverse=True)
    ft_candidates.sort(key=lambda x: x["prob"], reverse=True)
    return ht_candidates, ft_candidates

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.json().get("ok"):
            print("✅ Message successfully sent to Telegram!")
        else:
            print(f"❌ TELEGRAM API ERROR: {response.json()}")
    except Exception as e:
        print(f"❌ NETWORK ERROR: {e}")

if __name__ == "__main__":
    print("🤖 Starting daily AI scan...")
    all_ht_picks = []
    all_ft_picks = []
    
    for league in LEAGUES_TO_SCAN:
        print(f"Scanning {league}...")
        matches = get_fixtures(league)
        if not matches:
            continue
            
        ht_picks, ft_picks = filter_picks(matches)
        for p in ht_picks:
            p["league"] = league
        for p in ft_picks:
            p["league"] = league
            
        all_ht_picks.extend(ht_picks)
        all_ft_picks.extend(ft_picks)
    
    all_ht_picks.sort(key=lambda x: x["prob"], reverse=True)
    all_ft_picks.sort(key=lambda x: x["prob"], reverse=True)
    
    if all_ht_picks or all_ft_picks:
        message = f"🎯 *Daily AI Picks - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}*\n\n"
        
        # Top 3 HT Picks
        if all_ht_picks:
            message += "⚡ *Top 3 HT Under 1.5 Goals* ⚡\n"
            for p in all_ht_picks[:3]:
                message += f"⚽ {p['match']} ({p['league']})\n"
                message += f"⏰ {p['time']} | Prob: *{p['prob']}%*\n\n"
                
        # Top 3 FT Picks
        if all_ft_picks:
            message += "🛡️ *Top 3 FT Under 2.5 Goals (Safe)* 🛡️\n"
            for p in all_ft_picks[:3]:
                message += f"⚽ {p['match']} ({p['league']})\n"
                message += f"⏰ {p['time']} | Prob: *{p['prob']}%*\n\n"
        
        send_telegram_message(message)
    else:
        send_telegram_message("⚠️ No matches met the 70% criteria today.")
