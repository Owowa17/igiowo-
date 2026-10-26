import os
import requests
import time
import json
from datetime import datetime, timezone, timedelta

FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

LEAGUES_TO_SCAN = [
    "premier", "england2", "spain1", "italy1", "italy2", 
    "germany2", "france2", "turkey2", "brazil1", "argentina1"
]
HISTORY_FILE = "picks_history.json"

def get_api_data(league_key, date_str=None):
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    if date_str:
        url += f"?date={date_str}"
        
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=60)
            r.raise_for_status()
            return r.json().get("matches", [])
        except Exception as e:
            print(f"Attempt {attempt+1} failed for {league_key}: {e}")
            time.sleep(5)
    return []

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.json().get("ok"):
            print("✅ Message sent to Telegram!")
        else:
            print(f"❌ TELEGRAM ERROR: {response.json()}")
    except Exception as e:
        print(f"❌ NETWORK ERROR: {e}")

# --- 1. RESULTS TRACKER ---
def track_results():
    if not os.path.exists(HISTORY_FILE):
        print("No history file found. Skipping tracker.")
        return

    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    pending_picks = [p for p in history if p["date"] == yesterday and p["status"] == "pending"]

    if not pending_picks:
        print("No pending picks from yesterday.")
        return

    print(f"Tracking {len(pending_picks)} picks from yesterday...")
    results_msg = f"📊 *Results for {yesterday}*\n\n"
    wins = 0
    total = 0

    for pick in pending_picks:
        # Fetch yesterday's match data to get the score
        matches = get_api_data(pick["league"], yesterday)
        match_found = False
        
        for m in matches:
            match_name = f"{m.get('home_team')} vs {m.get('away_team')}"
            if match_name == pick["match"]:
                match_found = True
                score = m.get("score", {})
                ft_home = score.get("fulltime", {}).get("home")
                ft_away = score.get("fulltime", {}).get("away")
                ht_home = score.get("halftime", {}).get("home")
                ht_away = score.get("halftime", {}).get("away")

                if ft_home is None or ft_away is None:
                    results_msg += f"⏳ {match_name} - Score unavailable\n"
                    break

                total += 1
                is_win = False
                
                if pick["market"] == "HT Under 1.5":
                    if ht_home + ht_away <= 1:
                        is_win = True
                elif pick["market"] == "FT Under 2.5":
                    if ft_home + ft_away <= 2:
                        is_win = True
                
                if is_win:
                    wins += 1
                    pick["status"] = "WIN"
                    results_msg += f"✅ {match_name} - WON ({ht_home}-{ht_away} HT | {ft_home}-{ft_away} FT)\n"
                else:
                    pick["status"] = "LOSS"
                    results_msg += f"❌ {match_name} - LOST ({ht_home}-{ht_away} HT | {ft_home}-{ft_away} FT)\n"
                break
        
        if not match_found:
            results_msg += f"⚠️ {pick['match']} - Match data not found\n"

    if total > 0:
        results_msg += f"\n🏆 *Win Rate:* {wins}/{total} ({(wins/total)*100:.1f}%)"
        send_telegram_message(results_msg)

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

# --- 2. NEW PICKS GENERATOR ---
def generate_picks():
    print("🤖 Starting daily AI scan...")
    all_ht_picks = []
    all_ft_picks = []
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    for league in LEAGUES_TO_SCAN:
        matches = get_api_data(league)
        for match in matches:
            preds = match.get("model_predictions", {}).get("dc_v2", {}).get("calibrated", {})
            
            # HT Under 1.5
            ht_over = preds.get("ht_over_1.5")
            if ht_over and (1.0 - float(ht_over)) * 100 >= 70:
                all_ht_picks.append({
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "time": match.get("time", "N/A"),
                    "prob": round((1.0 - float(ht_over)) * 100, 1),
                    "market": "HT Under 1.5",
                    "league": league,
                    "date": today_date,
                    "status": "pending"
                })
                
            # FT Under 2.5
            ft_over = preds.get("over_2.5")
            if ft_over and (1.0 - float(ft_over)) * 100 >= 70:
                all_ft_picks.append({
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "time": match.get("time", "N/A"),
                    "prob": round((1.0 - float(ft_over)) * 100, 1),
                    "market": "FT Under 2.5",
                    "league": league,
                    "date": today_date,
                    "status": "pending"
                })

    all_ht_picks.sort(key=lambda x: x["prob"], reverse=True)
    all_ft_picks.sort(key=lambda x: x["prob"], reverse=True)
    
    top_ht = all_ht_picks[:3]
    top_ft = all_ft_picks[:3]
    
    if top_ht or top_ft:
        message = f"🎯 *Daily AI Picks - {today_date}*\n\n"
        if top_ht:
            message += "⚡ *Top 3 HT Under 1.5* ⚡\n"
            for p in top_ht:
                message += f"⚽ {p['match']} ({p['league']})\n⏰ {p['time']} | *{p['prob']}%*\n\n"
        if top_ft:
            message += "🛡️ *Top 3 FT Under 2.5* 🛡️\n"
            for p in top_ft:
                message += f"⚽ {p['match']} ({p['league']})\n⏰ {p['time']} | *{p['prob']}%*\n\n"
        send_telegram_message(message)
    else:
        send_telegram_message("⚠️ No matches met the 70% criteria today.")

    # Save to History
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
            
    history.extend(top_ht)
    history.extend(top_ft)
    
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

if __name__ == "__main__":
    track_results()    # 1. First, check yesterday's results
    generate_picks()   # 2. Then, scan for today's picks
