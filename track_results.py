import os
import requests
import json
import time
import sys
from datetime import datetime, timezone, timedelta

# --- CONFIGURATION ---
FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
HISTORY_FILE = "picks_history.json"

def send_telegram_message(text):
    """Sends a formatted message to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Telegram credentials missing.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.json().get("ok"):
            print("📤 Scorecard sent to Telegram!")
        else:
            print(f"❌ Telegram Error: {response.text}")
    except Exception as e:
        print(f"❌ Telegram Network Error: {e}")

def clean_name(name):
    """Removes common suffix/prefix noise for better matching."""
    if not name: return ""
    name = name.lower()
    # Remove common suffixes that might differ in API vs history
    for noise in ["fc", "afc", "sc", "united", "city", "town", "athletic", "rovers", "wanderers"]:
        name = name.replace(noise, "")
    return name.strip()

def get_match_result(match_name, match_date, league_key):
    """
    Fetches finished results from Football-Charts API for a specific league.
    Matches the game by comparing cleaned team names.
    """
    # The API endpoint for finished results
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/results/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    try:
        # We don't need the full season, just recent results
        params = {"season": datetime.now().year} # Adjust if your API uses a different season format
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        
        # The API returns a list of finished matches
        matches = data.get("matches", [])
        
        # Split the history name "Home vs Away"
        if " vs " not in match_name:
            return None
            
        hist_home, hist_away = match_name.split(" vs ")
        clean_hist_home = clean_name(hist_home)
        clean_hist_away = clean_name(hist_away)
        
        for match in matches:
            api_home = match.get("home_team", "")
            api_away = match.get("away_team", "")
            
            # Check if the team names match (using 'in' for partial matching)
            if (clean_hist_home in clean_name(api_home) or clean_name(api_home) in clean_hist_home) and \
               (clean_hist_away in clean_name(api_away) or clean_name(api_away) in clean_hist_away):
                
                # Verify the date (API returns kickoff_utc or date)
                api_date = str(match.get("date", ""))[:10]
                if api_date == match_date:
                    ft_home = match.get("home_score", 0)
                    ft_away = match.get("away_score", 0)
                    # Try to get Half-Time score if available
                    ht_home = match.get("ht_home_score")
                    ht_away = match.get("ht_away_score")
                    
                    return {
                        "ft_home": int(ft_home),
                        "ft_away": int(ft_away),
                        "ht_home": int(ht_home) if ht_home is not None else None,
                        "ht_away": int(ht_away) if ht_away is not None else None
                    }
                    
    except Exception as e:
        print(f"⚠️ API Error for {league_key}: {e}")
        
    return None

def check_bet_result(market, scores):
    """Calculates if the bet won or lost based on the score."""
    if scores is None:
        return "PENDING" # Match not found or not finished yet
        
    ft_total = scores["ft_home"] + scores["ft_away"]
    
    if market == "HT Under 1.5":
        # Check if HT scores exist
        if scores["ht_home"] is not None:
            ht_total = scores["ht_home"] + scores["ht_away"]
            return "WON" if ht_total < 1.5 else "LOST"
        else:
            # If HT score is missing, assume worst case (loss) or leave pending
            return "LOST" 
            
    elif market == "FT Under 2.5":
        return "WON" if ft_total < 2.5 else "LOST"
        
    elif "Double Chance (1X)" in market:
        return "WON" if scores["ft_home"] >= scores["ft_away"] else "LOST"
        
    elif "Double Chance (X2)" in market:
        return "WON" if scores["ft_away"] >= scores["ft_home"] else "LOST"
        
    return "PENDING"

def track_results():
    print("🕵️ Starting Result Tracker...")
    
    if not os.path.exists(HISTORY_FILE):
        print("No history file found.")
        sys.exit(0)

    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)

    updated_history = []
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    
    wins = 0
    losses = 0
    total_monthly_picks = 0
    monthly_wins = 0

    for pick in history:
        # Only check picks that are pending and are from today or yesterday
        if pick.get("result", "PENDING") == "PENDING" and pick["date"] in [today_str, yesterday_str]:
            
            league = pick.get("league", "").lower()
            # If league is missing, we can't fetch results
            if not league:
                print(f"⚠️ Missing league for {pick['match']}, skipping.")
                updated_history.append(pick)
                continue
            
            print(f"🔍 Checking {pick['match']} ({league})...")
            scores = get_match_result(pick["match"], pick["date"], league)
            
            if scores:
                result = check_bet_result(pick["market"], scores)
                pick["result"] = result
                
                if result == "WON": 
                    wins += 1
                    print(f"   ✅ WON: {pick['match']}")
                elif result == "LOST":
                    losses += 1
                    print(f"   ❌ LOST: {pick['match']}")
                    
        updated_history.append(pick)
        
        # Calculate monthly stats
        if pick.get("result") in ["WON", "LOST"] and pick["date"][:7] == today_str[:7]:
            total_monthly_picks += 1
            if pick["result"] == "WON":
                monthly_wins += 1

    # Save the updated history
    with open(HISTORY_FILE, "w") as f:
        json.dump(updated_history, f, indent=4)

    # Generate Scorecard
    win_rate = (monthly_wins / total_monthly_picks * 100) if total_monthly_picks > 0 else 0
    
    # Only send message if we actually graded something or if there are stats to show
    if wins > 0 or losses > 0:
        message = f"📊 <b>Daily Result Scorecard</b>\n\n"
        message += f"✅ Wins: {wins}\n"
        message += f"❌ Losses: {losses}\n\n"
        message += f"📈 <b>Monthly Win Rate:</b> {win_rate:.1f}% ({monthly_wins}/{total_monthly_picks})\n\n"
        
        if win_rate >= 70:
            message += "🔥 <b>Our AI is on fire! Keep winning!</b>\n"
        else:
            message += "💎 <b>Consistency is key. We protect the bankroll.</b>\n"
            
        message += (
            "\n🔥 <b>Ready for tomorrow's picks?</b>\n"
            "👉 <a href='https://1win.ng/?p=q70f'>Register on 1win Here</a>\n"
            "🎁 <b>Promo Code: <code>IGIOWO</code></b>\n"
        )
        send_telegram_message(message)
    else:
        print("ℹ️ No finished matches to grade today. Skipping Telegram message.")

if __name__ == "__main__":
    track_results()
