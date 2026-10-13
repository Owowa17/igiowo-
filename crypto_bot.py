import ccxt
import time
import requests
import pandas as pd
import ta

T = "https://ntfy.sh/igiowo_x7k2m9p4q8n3w6t1v5j0a2d7"
S = "BTC/USDT"
TF = "1m"
SM = 4
EXP = 2
COOL = 3
AM = 30

ex = None
for n in ["binance", "bybit", "okx"]:
    try:
        ex = getattr(ccxt, n)()
        ex.fetch_ohlcv(S, TF, 5)
        print("Using:", n)
        break
    except Exception as e:
        print("Exchange failed:", n, str(e)[:60])

if not ex:
    raise SystemExit("No exchange available")

print("Bot started")
last_ts = 0
cool = 0
pend = []
wins = 0
losses = 0


def fetch_candles():
    d = ex.fetch_ohlcv(S, TF, 250)
    df = pd.DataFrame(d, columns=["t", "o", "h", "l", "c", "v"])
    df = df.iloc[:-1].reset_index(drop=True)
    df["rsi"] = ta.momentum.RSIIndicator(df["c"], 14).rsi()
    df["e20"] = ta.trend.EMAIndicator(df["c"], 20).ema_indicator()
    df["e50"] = ta.trend.EMAIndicator(df["c"], 50).ema_indicator()
    bb = ta.volatility.BollingerBands(df["c"], 20, 2)
    df["bbu"] = bb.bollinger_hband()
    df["bbl"] = bb.bollinger_lband()
    adx = ta.trend.ADXIndicator(df["h"], df["l"], df["c"], 14)
    df["adx"] = adx.adx()
    df["vavg"] = df["v"].rolling(20).mean()
    return df


def settle_pending(df, ts):
    global wins, losses
    for s in pend[:]:
        et = s["ts"] + EXP * 60000
        if ts < et:
            continue
        m = df.loc[df["t"] == et, "c"]
        if len(m):
            px = m.iloc[0]
            w = (px > s["px"]) if s["dir"] == "CALL" else (px < s["px"])
            wins += int(w)
            losses += int(not w)
            tot = wins + losses
            res = "WIN" if w else "LOSS"
            print(f"\nRes {s['dir']}: {res} | {wins}/{tot} = {wins/tot:.0%}")
        pend.remove(s)


def check_signal(df):
    global cool
    L = df.iloc[-1]
    P = df.iloc[-2]
    bd = abs(L.c - L.o) or 1e-9
    lo = min(L.o, L.c) - L.l
    up = L.h - max(L.o, L.c)
    rmin = df["rsi"].iloc[-3:].min()
    rmax = df["rsi"].iloc[-3:].max()
    clm = L.adx < AM
    vol = L.v > 1.2 * L.vavg

    cm = P.c <= P.bbl and L.c > L.bbl
    cl = sum([rmin <= 32, L.c > L.o, lo > bd, L.e20 >= L.e50, vol, clm])

    pm = P.c >= P.bbu and L.c < P.bbu
    pt = sum([rmax >= 68, L.c < L.o, up > bd, L.e20 <= L.e50, vol, clm])

    sig = None
    if cm and cl >= SM and cl > pt:
        sig = "CALL"
    if pm and pt >= SM and pt > cl:
        sig = "PUT"

    if sig:
        msg = f"{sig} {S} | score {cl}/6 | px {L.c:.2f} | RSI {L.rsi:.0f}"
        print("\nSIGNAL:", msg)
        try:
            requests.post(T, data=msg.encode(), timeout=10)
        except Exception as e:
            print("Notify failed:", str(e)[:80])
        pend.append({"ts": int(L.t), "dir": sig, "px": L.c})
        cool = COOL
    else:
        print(".", end="")


while True:
    try:
        df = fetch_candles()
        ts = int(df["t"].iloc[-1])

        if ts != last_ts:
            last_ts = ts
            settle_pending(df, ts)

            if cool > 0:
                cool -= 1
                print("c", end="")
            else:
                check_signal(df)

    except KeyboardInterrupt:
        print("\nStopped by user")
        break
    except Exception as e:
        print("\nErr:", str(e)[:100])
        time.sleep(5)

    time.sleep(max(1, 62 - (time.time() % 60)))
