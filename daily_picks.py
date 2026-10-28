import os
import requests
import time
import json
from datetime import datetime, timezone

FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

LEAGUES_TO_SCAN = ["turkey2", "germany2", "premier"]
HISTORY_FILE = "picks_history.json"

def get_fixtures(league_key):
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=20) 
            r.raise_for_status()
            data = r.json()
            matches = data.get("matches", [])
            
            # Freshness Check (Still ensures the API itself updated recently)
            if matches:
                computed_at_str = matches[0].get("computed_at")
                if computed_at_str:
                    computed_date = computed_at_str.split("T")[0]
                    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if computed_date != today_utc:
                        print(f"⚠️ Data for {league_key} is stale. API hasn't refreshed yet.")
                        return None 
            return matches 
        except Exception as e:
            print(f"Attempt {attempt+1} failed for {league_key}: {e}")
            time.sleep(5) 
            
    return None 

def filter_picks(matches):
    ht_candidates = []
    ft_candidates = []
    
    for match in matches:
        # Extract date safely. If API doesn't provide it, default to today.
        raw_date = match.get("date") or match.get("match_date") or match.get("start_time") or match.get("time") or ""
        # Try to extract YYYY-MM-DD. If it's just "15:00", assume it's today.
        if len(str(raw_date)) >= 10:
            match_date = str(raw_date)[:10]
        else:
            match_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        model_preds = match.get("model_predictions") or {}
        dc_v2 = model_preds.get("dc_v2") or {}
        preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
        
        match_name = f"{match['home_team']} vs {match['away_team']}"
        match_time = match.get("time", "N/A")
        
        # --- HT Under 1.5 ---
        ht_over = preds.get("ht_over_1.5")
        if ht_over:
            ht_under = (1.0 - float(ht_over)) * 100
            if ht_under >= 70:
                ht_candidates.append({
                    "match": match_name,
                    "time": match_time,
                    "prob": round(ht_under, 1),
                    "date": match_date
                })
                
        # --- FT Under 2.5 ---
        ft_over = preds.get("ft_over_2.5")
        if ft_over:
            ft_under = (1.0 - float(ft_over)) * 100
            if ft_under >= 65: 
                ft_candidates.append({
                    "match": match_name,
                    "time": match_time,
                    "prob": round(ft_under, 1),
                    "date": match_date
                })
    
    return ht_candidates, ft_candidates

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
        if not response.json().get("ok"):
            print(f"❌ TELEGRAM API ERROR: {response.json()}")
    except Exception as e:
        print(f"❌ NETWORK ERROR: {e}")

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
        for p in ht_picks: p["league"] = league
        for p in ft_picks: p["league"] = league
            
        all_ht_picks.extend(ht_picks)
        all_ft_picks.extend(ft_picks)
    
    # Sort by Date (Earliest first), then by Probability (Highest first)
    all_ht_picks.sort(key=lambda x: (x["date"], -x["prob"]))
    all_ft_picks.sort(key=lambda x: (x["date"], -x["prob"]))
    
    # Determine the target date (the earliest date we have a pick for)
    target_date = None
    if all_ht_picks:
        target_date = all_ht_picks[0]["date"]
    elif all_ft_picks:
        target_date = all_ft_picks[0]["date"]
        
    if target_date:
        # Filter ONLY for the target date, and take the top 3
        top_ht = [p for p in all_ht_picks if p["date"] == target_date][:3]
        top_ft = [p for p in all_ft_picks if p["date"] == target_date][:3]
        
        # Adjust header based on whether the picks are for today or a future date
        if target_date == today_date:
            header_title = f"🎯 <b>Daily AI Picks - {today_date}</b>"
        else:
            header_title = f"🎯 <b>Early AI Picks for {target_date}</b>"
            
        message = f"{header_title}\n\n"
        
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

        # Monetization Block
        message += "🔥 <b>Ready to place your bets?</b>\n"
        message += "Join 1win using our exclusive code to get the best odds!\n\n"
        message += f"👉 <a href='https://1win.ng/?p=q70f'>Click here to Register on 1win</a>\n"
        message += "🎁 <b>Promo Code: <code>IGIOWO</code></b>\n"
        
        send_telegram_message(message)
        
        # Save History
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f: history = json.load(f)
            except: pass
        history.extend(top_ht)
        history.extend(top_ft)
        with open(HISTORY_FILE, "w") as f: json.dump(history, f, indent=4)

    else:
        if api_failed_or_stale:
            print("Skipping Telegram send. API failed or data is stale.")
        else:
            # True Rest Day (No picks found in the entire API response)
            rest_message = (
                "🧠 <b>DAILY AI UPDATE</b> 🧠\n\n"
                "⚠️ <b>No High-Value Picks Found.</b>\n\n"
                "Our AI scanned all upcoming fixtures and found no matches meeting our strict probability criteria. \n\n"
                "💎 <b>Protecting your bankroll:</b> We never force bad bets. We only strike when the math gives us a massive edge.\n\n"
                "🔥 <b>Be ready for the next drop:</b>\n"
                f"👉 <a href='https://1win.ng/?p=q70f'>Register on 1win Here</a>\n"
                "🎁 <b>Promo Code: <code>IGIOWO</code></b>"
            )
            send_telegram_message(rest_message)

if __name__ == "__main__":
    generate_picks()
