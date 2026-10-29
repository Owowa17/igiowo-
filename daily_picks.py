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

def get_fixtures(league_key):
    url = f"https://footballcharts-backend.onrender.com/api/v1/leagues/{league_key}/fixtures/"
    headers = {"Authorization": f"Bearer {FC_API_KEY}"}
    
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=20) 
            r.raise_for_status()
            data = r.json()
            matches = data.get("matches", [])
            
            if matches:
                computed_at_str = matches[0].get("computed_at")
                if computed_at_str:
                    computed_date = computed_at_str.split("T")[0]
                    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if computed_date != today_utc:
                        print(f"⚠️ Data for {league_key} is stale.")
                        return None 
            return matches 
        except Exception as e:
            print(f"Attempt {attempt+1} failed for {league_key}: {e}")
            time.sleep(5) 
            
    return None 

def filter_picks(matches):
    ht_candidates = []
    ft_candidates = []
    dc_candidates = []
    
    for match in matches:
        raw_date = match.get("date") or match.get("match_date") or match.get("start_time") or match.get("time") or ""
        if len(str(raw_date)) >= 10:
            match_date = str(raw_date)[:10]
        else:
            match_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        model_preds = match.get("model_predictions") or {}
        dc_v2 = model_preds.get("dc_v2") or {}
        preds = dc_v2.get("calibrated") or dc_v2.get("raw") or {}
        print(f"DEBUG DATA for {match['home_team']}: {preds.keys()}")
        match_name = f"{match['home_team']} vs {match['away_team']}"
        match_time = match.get("time", "N/A")
        
        # --- HT Under 1.5 ---
        ht_over = preds.get("ht_over_1.5")
        if ht_over:
            ht_under = (1.0 - float(ht_over)) * 100
            if ht_under >= 70:
                ht_candidates.append({
                    "match": match_name, "time": match_time, "prob": round(ht_under, 1), "date": match_date, "market": "HT Under 1.5"
                })
                
        # --- FT Under 2.5 ---
        ft_over = preds.get("ft_over_2.5")
        if ft_over:
            ft_under = (1.0 - float(ft_over)) * 100
            if ft_under >= 65: 
                ft_candidates.append({
                    "match": match_name, "time": match_time, "prob": round(ft_under, 1), "date": match_date, "market": "FT Under 2.5"
                })

        # --- DOUBLE CHANCE (1X or X2) ---
        # Try to get direct DC probabilities, or calculate from 1X2
        dc_1x = preds.get("dc_1x") or preds.get("1x")
        dc_x2 = preds.get("dc_x2") or preds.get("x2")
        
        # Fallback: If API doesn't have DC, calculate it from 1X2
        if not dc_1x and not dc_x2:
            p_home = preds.get("ft_home_win") or preds.get("home_win") or preds.get("1")
            p_draw = preds.get("ft_draw") or preds.get("draw") or preds.get("x")
            p_away = preds.get("ft_away_win") or preds.get("away_win") or preds.get("2")
            
            if p_home and p_draw:
                dc_1x = float(p_home) + float(p_draw)
            if p_away and p_draw:
                dc_x2 = float(p_away) + float(p_draw)
                
        # Check 1X Probability (Threshold 80%)
        if dc_1x:
            dc_1x_prob = float(dc_1x) * 100 if float(dc_1x) <= 1.0 else float(dc_1x)
            if dc_1x_prob >= 80:
                dc_candidates.append({
                    "match": match_name, "time": match_time, "prob": round(dc_1x_prob, 1), "date": match_date, "market": "Double Chance (1X)"
                })
                
        # Check X2 Probability (Threshold 80%)
        if dc_x2:
            dc_x2_prob = float(dc_x2) * 100 if float(dc_x2) <= 1.0 else float(dc_x2)
            if dc_x2_prob >= 80:
                dc_candidates.append({
                    "match": match_name, "time": match_time, "prob": round(dc_x2_prob, 1), "date": match_date, "market": "Double Chance (X2)"
                })
                
    return ht_candidates, ft_candidates, dc_candidates

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if not response.json().get("ok"):
            print(f"❌ TELEGRAM API ERROR: {response.json()}")
    except Exception as e:
        print(f"❌ NETWORK ERROR: {e}")

def generate_picks():
    print("🤖 Starting daily AI scan...")
    all_ht, all_ft, all_dc = [], [], []
    api_failed_or_stale = False
    today_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    # Load History to prevent duplicate picks
    picked_matches = set()
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history_data = json.load(f)
                for item in history_data:
                    picked_matches.add(item.get("match"))
        except Exception as e:
            print(f"Could not load history: {e}")
    
    for league in LEAGUES_TO_SCAN:
        print(f"Scanning {league}...")
        matches = get_fixtures(league)
        if matches is None:
            api_failed_or_stale = True
            continue
            
        ht, ft, dc = filter_picks(matches)
        
        # Filter out matches already in history
        ht = [p for p in ht if p["match"] not in picked_matches]
        ft = [p for p in ft if p["match"] not in picked_matches]
        dc = [p for p in dc if p["match"] not in picked_matches]
        
        for p in ht: p["league"] = league
        for p in ft: p["league"] = league
        for p in dc: p["league"] = league
            
        all_ht.extend(ht)
        all_ft.extend(ft)
        all_dc.extend(dc)
    
    # Sort by Date, then Probability
    all_ht.sort(key=lambda x: (x["date"], -x["prob"]))
    all_ft.sort(key=lambda x: (x["date"], -x["prob"]))
    all_dc.sort(key=lambda x: (x["date"], -x["prob"]))
    
    # Find the earliest target date across all markets
    dates = [p["date"] for p in all_ht + all_ft + all_dc]
    target_date = min(dates) if dates else None
        
    if target_date:
        top_ht = [p for p in all_ht if p["date"] == target_date][:3]
        top_ft = [p for p in all_ft if p["date"] == target_date][:3]
        top_dc = [p for p in all_dc if p["date"] == target_date][:3]
        
        header_title = f"🎯 <b>Daily AI Picks - {today_date}</b>" if target_date == today_date else f"🎯 <b>Early AI Picks for {target_date}</b>"
        message = f"{header_title}\n\n"
        
        if top_ht:
            message += "⚡ <b>Top HT Under 1.5</b> ⚡\n"
            for p in top_ht:
                message += f"⚽ {p['match']} ({p['league']})\n⏰ {p['time']} | <b>{p['prob']}%</b>\n\n"
                
        if top_ft:
            message += "🛡️ <b>Top FT Under 2.5</b> 🛡️\n"
            for p in top_ft:
                message += f"⚽ {p['match']} ({p['league']})\n⏰ {p['time']} | <b>{p['prob']}%</b>\n\n"

        if top_dc:
            message += "💎 <b>Top Double Chance (Safest)</b> 💎\n"
            for p in top_dc:
                # Extract just the market part for display (e.g., "1X" or "X2")
                market_type = p['market'].replace("Double Chance ", "")
                message += f"⚽ {p['match']} ({p['league']})\n⏰ {p['time']} | <b>{market_type} @ {p['prob']}%</b>\n\n"

        # Monetization Block
        message += "🔥 <b>Ready to place your bets?</b>\n"
        message += "Join 1win using our exclusive code to get the best odds!\n\n"
        message += f"👉 <a href='https://1win.ng/?p=q70f'>Click here to Register on 1win</a>\n"
        message += "🎁 <b>Promo Code: <code>IGIOWO</code></b>\n"
        
        send_telegram_message(message)
        
        # Save to History
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f: history = json.load(f)
            except: pass
        history.extend(top_ht)
        history.extend(top_ft)
        history.extend(top_dc)
        with open(HISTORY_FILE, "w") as f: json.dump(history, f, indent=4)

    else:
        if api_failed_or_stale:
            print("Skipping Telegram send. API failed or data is stale.")
        else:
            rest_message = (
                "🧠 <b>DAILY AI UPDATE</b> 🧠\n\n"
                "⚠️ <b>No High-Value Picks Found.</b>\n\n"
                "Our AI scanned all upcoming fixtures and found no matches meeting our strict probability criteria. \n\n"
                "💎 <b>Protecting your bankroll:</b> We never force bad bets.\n\n"
                "🔥 <b>Be ready for the next drop:</b>\n"
                f"👉 <a href='https://1win.ng/?p=q70f'>Register on 1win Here</a>\n"
                "🎁 <b>Promo Code: <code>IGIOWO</code></b>"
            )
            send_telegram_message(rest_message)

if __name__ == "__main__":
    generate_picks()
