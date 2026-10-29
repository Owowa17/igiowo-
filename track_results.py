import os
import requests
import json
import time
from datetime import datetime, timezone, timedelta

# --- CONFIGURATION ---
FC_API_KEY = os.environ.get("FC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
HISTORY_FILE = "picks_history.json"

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
        print("📤 Scorecard sent to Telegram!")
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

def get_match_result(match_name, match_date):
    """
    This function attempts to get the score. 
    NOTE: You must replace this with your specific API's results endpoint.
    """
    # --- PLACEHOLDER FOR YOUR API ---
    # If you have a results endpoint, put it here. 
    # Example: url = f"https://footballcharts-backend.onrender.com/api/v1/results/?date={match_date}"
    
    # For demonstration, we will simulate a successful fetch.
    # You will need to parse the JSON to get the 'home_score' and 'away_score'.
    # Let's pretend the API returned these scores based on a hash of the name:
    if "Arsenal" in match_name:
        return 1, 0  # Home 1, Away 0
    elif "Chelsea" in match_name:
        return 2, 2  # Home 2, Away 2
    else:
        return 0, 0  # Default simulated score

def check_bet_result(market, home_score, away_score):
    """Calculates if the bet won or lost based on the score."""
    total_goals = home_score + away_score
    
    if market == "HT Under 1.5":
        # (Note: Ideally, you need the Half-Time score, not Full-Time. 
        # If the API only gives FT, we have to assume the worst-case scenario)
        ht_goals = total_goals / 2 # Simulating HT goals for demo
        return "WON" if ht_goals < 1.5 else "LOST"
        
    elif market == "FT Under 2.5":
        return "WON" if total_goals < 2.5 else "LOST"
        
    elif "Double Chance (1X)" in market:
        return "WON" if home_score >= away_score else "LOST"
        
    elif "Double Chance (X2)" in market:
        return "WON" if away_score >= home_score else "LOST"
        
    return "PENDING"

def track_results():
    print("🕵️ Starting Result Tracker...")
    
    if not os.path.exists(HISTORY_FILE):
        print("No history file found.")
        return

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
        # If the pick has no result yet, we check it
        if "result" not in pick or pick["result"] == "PENDING":
            
            # We only check matches that happened today or yesterday (to avoid checking old ones)
            if pick["date"] in [today_str, yesterday_str]:
                home_score, away_score = get_match_result(pick["match"], pick["date"])
                
                # You can add a check here to ensure the match has actually FINISHED
                # if not finished, continue
                
                result = check_bet_result(pick["market"], home_score, away_score)
                pick["result"] = result
                
                if result == "WON": wins += 1
                if result == "LOST": losses += 1
                
        updated_history.append(pick)
        
        # Calculate monthly stats (assuming dates are YYYY-MM-DD)
        if pick.get("result") in ["WON", "LOST"] and pick["date"][:7] == today_str[:7]:
            total_monthly_picks += 1
            if pick["result"] == "WON":
                monthly_wins += 1

    # Save the updated history
    with open(HISTORY_FILE, "w") as f:
        json.dump(updated_history, f, indent=4)

    # Generate Scorecard
    win_rate = (monthly_wins / total_monthly_picks * 100) if total_monthly_picks > 0 else 0
    
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
        print("No finished matches to grade today.")

if __name__ == "__main__":
    track_results()
