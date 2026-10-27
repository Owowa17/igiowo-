import os
import requests
import time
import json
from datetime import datetime, timezone

# --- CONFIGURATION ---
FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

LEAGUES_TO_SCAN = ["turkey2", "germany2", "premier"]
HISTORY_FILE = "picks_history.json"

# --- FETCH DATA ---
def get_fixtures(league_key):
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    for attempt in range(3):
        try:
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
                        return None 
                        
            return matches 
        except Exception as e:
            print(f"Attempt {attempt+1} failed for {league_key}: {e}")
            time.sleep(5) 
            
    print(f"Failed to fetch {league_key} after 3 attempts.")
    return None 

def filter_picks(matches):
    ht_candidates = []
    ft_candidates = []
    
    # Get today's date in UTC format (YYYY-MM-DD)
    today_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    for match in matches:
        # --- 1. DATE FILTER (New Addition) ---
        # Try to find the date field. It could be named 'date', 'match_date', 'kickoff', or 'start_time'
        match_date = match.get("date") or match.get("match_date") or match.get("kickoff") or match.get("start_time")
        
        # If the API only has "time" (e.g., "15:00"), we might need to check the raw data. 
        # Assuming the API provides a full date string somewhere (like "2026-10-27T15:00:00Z"):
        if match_date:
            # Check if today's date is in the match date string
            if today_utc_str not in str(match_date):
                continue # Skip this match, it's not today
        else:
            # If we can't find a date field, we might want to print a warning to debug
            # print(f"Warning: No date found for {match.get('home_team')}")
            pass # Proceed if we can't find a date, but we recommend checking the API response

        # --- 2. EXISTING FILTER LOGIC ---
        model_preds = match.get("model_predictions") or {}
        dc_v2 = model_preds.get("dc_v2") or {}
        preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
        
        match_name = f"{match['home_team']} vs {match['away_team']}"
        
        # Extract time for display
        match_time = match.get("time", "N/A") 
        
        # --- HT Under 1.5 ---
        ht_over = preds.get("ht_over_1.5")
        if ht_over:
            ht_under = (1.0 - float(ht_over)) * 100
            if ht_under >= 70:
                ht_candidates.append({
                    "match": match_name,
                    "time": match_time,
                    "prob": round(ht_under, 1)
                })
                
        # --- FT Under 2.5 ---
        ft_over = preds.get("ft_over_2.5")
        if ft_over:
            ft_under = (1.0 - float(ft_over)) * 100
            if ft_under >= 65: 
                ft_candidates.append({
                    "match": match_name,
                    "time": match_time,
                    "prob": round(ft_under, 1)
                })
    
    return ht_candidates, ft_candidates

# --- SEND TELEGRAM ---
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
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

# --- TRACK RESULTS (Placeholder) ---
def track_results():
    # Place your result tracking logic here if you have specific API calls for it.
    # For now, this will safely print a message so the script doesn't crash.
    print("📊 Checking yesterday's results...")
    if os.path.exists(HISTORY_FILE):
         print(f"History file found with size: {os.path.getsize(HISTORY_FILE)} bytes")
    else:
         print("No history file found yet. It will be created today.")

# --- MAIN GENERATION LOGIC ---
def generate_picks():
    print("🤖 Starting daily AI scan...")
    all_ht_picks = []
    all_ft_picks = []
    api_failed_or_stale = False
    today_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    for league in LEAGUES_TO_SCAN:
        print(f"Scanning {league}...")
        matches = get_fixtures(league)
        
        if matches is None:
            api_failed_or_stale = True
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
    
    top_ht = all_ht_picks[:3]
    top_ft = all_ft_picks[:3]
    
    if top_ht or top_ft:
        message = f"🎯 <b>Daily AI Picks - {today_date}</b>\n\n"
        
        if top_ht:
            message += "⚡ <b>Top 3 HT Under 1.5</b> ⚡\n"
            for p in top_ht:
                message += f"⚽ {p['match']} ({p['league']})\n"
                message += f"⏰ {p['time']} | HT Under 1.5: <b>{p['prob']}%</b>\n\n"
                
        if top_ft:
            message += "🛡️ <b>Top 3 FT Under 2.5</b> 🛡️\n"
            for p in top_ft:
                message += f"⚽ {p['match']} ({p['league']})\n"
                message += f"⏰ {p['time']} | FT Under 2.5: <b>{p['prob']}%</b>\n\n"

        # --- MONETIZATION BLOCK START ---
        message += "🔥 <b>Ready to place your bets?</b>\n"
        message += "Join 1win using our exclusive code to get the best odds!\n\n"
        message += f"👉 <a href='https://1win.ng/?p=q70f'>Click here to Register on 1win</a>\n"
        message += "🎁 <b>Promo Code: <code>IGIOWO</code></b>\n"
        # --- MONETIZATION BLOCK END ---
        
        send_telegram_message(message)
        
    else:
        if api_failed_or_stale:
            print("Skipping Telegram send. Data is not fresh yet or API failed.")
        else:
            send_telegram_message("⚠️ No matches met the 70% criteria today.")
            
    # --- SAVE TO HISTORY ---
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        except json.JSONDecodeError:
            pass # Prevents crashing if the file is empty/corrupted
            
    history.extend(top_ht)
    history.extend(top_ft)
    
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

# --- EXECUTION ---
if __name__ == "__main__":
    track_results()      # 1. First, check yesterday's results
    generate_picks()     # 2. Then, scan for today's picks
