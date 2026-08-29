import base64
import datetime
import json
import time
import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

from data_fetcher import fetch_60min_kline, get_realtime_dde
import mobile_adapter as ma  # 導入整個 mobile_adapter
from risk_card import render_risk_card


def render_html_iframe(
    html_code: str, height: int = 600, scrolling: bool = False
):
    """將 HTML 字串轉為 base64 URI 並透過 st.iframe 渲染，避免 DeprecationWarning"""
    b64_html = base64.b64encode(html_code.encode("utf-8")).decode("utf-8")
    data_url = f"data:text/html;charset=utf-8;base64,{b64_html}"
    st.iframe(src=data_url, height=height, scrolling=scrolling)


# ---------------------------------------------------------
# 1. 頁面配置與樣式
# ---------------------------------------------------------
ma.init_mobile_adapter(page_title="台股 K 線監控站", show_tip=True)

st.markdown(
    """
    <style>
    .stock-info-card {
        background-color: #1E222D;
        padding: 12px;
        border-radius: 8px;
        border: 1px solid #2A2E39;
        margin-bottom: 12px;
    }
    .metric-title { font-size: 13px; color: #888888; font-weight: bold; margin-bottom: 5px; }
    .metric-row { display: flex; justify-content: space-between; font-size: 13px; padding: 3px 0; border-bottom: 1px dashed #2A2E39; }
    .metric-label { color: #CCCCCC; }
    .metric-value { font-weight: bold; color: #FFFFFF; }
    </style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------
# 2. 抓取股票數據與三大法人籌碼 (FinMind REST API)
# ---------------------------------------------------------
@st.cache_data(ttl=3600)
def get_stock_name(stock_code):
    clean_code = str(stock_code).strip().replace(".TW", "").replace(".TWO", "")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockInfo"}
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if data.get("msg") == "success":
            df_info = pd.DataFrame(data["data"])
            matched = df_info[df_info["stock_id"] == clean_code]
            if not matched.empty:
                return matched.iloc[0]["stock_name"]
    except Exception:
        pass
    return clean_code


@st.cache_data(ttl=3600)
def fetch_stock_meta_and_kline(stock_code):
    clean_code = str(stock_code).strip().replace(".TW", "").replace(".TWO", "")
    stock_name = get_stock_name(clean_code)

    end_date = datetime.date.today().strftime("%Y-%m-%d")
    start_date = (
        datetime.date.today() - datetime.timedelta(days=365)
    ).strftime("%Y-%m-%d")

    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": clean_code,
        "start_date": start_date,
        "end_date": end_date,
    }

    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        if data.get("msg") != "success" or not data.get("data"):
            return stock_name, clean_code, pd.DataFrame()

        df = pd.DataFrame(data["data"])

        df.rename(
            columns={
                "date": "DateStr",
                "open": "Open",
                "max": "High",
                "min": "Low",
                "close": "Close",
                "Trading_Volume": "Volume",
            },
            inplace=True,
        )

        df["Open"] = df["Open"].astype(float)
        df["High"] = df["High"].astype(float)
        df["Low"] = df["Low"].astype(float)
        df["Close"] = df["Close"].astype(float)
        df["Volume"] = (df["Volume"] / 1000).astype(int)

        return stock_name, clean_code, df

    except Exception as e:
        st.sidebar.error(f"資料讀取失敗: {e}")
        return stock_name, clean_code, pd.DataFrame()


@st.cache_data(ttl=300)
def fetch_institutional_data(stock_code):
    clean_code = str(stock_code).strip().replace(".TW", "").replace(".TWO", "")
    start_date = (
        datetime.date.today() - datetime.timedelta(days=15)
    ).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": clean_code,
        "start_date": start_date,
    }

    try:
        time.sleep(0.1)
        res = requests.get(url, params=params, timeout=8)
        data = res.json()

        if data.get("msg") != "success" or not data.get("data"):
            return pd.DataFrame(
                [["無資料", 0, 0, 0, 0]],
                columns=["日期", "外資", "投信", "自營商", "合計"],
            )

        df_raw = pd.DataFrame(data["data"])
        df_raw["buy_sell_sheet"] = (df_raw["buy"] - df_raw["sell"]) / 1000

        def categorize(name):
            if "Foreign" in name:
                return "外資"
            elif "Investment_Trust" in name:
                return "投信"
            elif "Dealer" in name:
                return "自營商"
            return "其他"

        df_raw["法人類別"] = df_raw["name"].apply(categorize)

        df_pivot = df_raw.pivot_table(
            index="date",
            columns="法人類別",
            values="buy_sell_sheet",
            aggfunc="sum",
        ).fillna(0)

        for col in ["外資", "投信", "自營商"]:
            if col not in df_pivot.columns:
                df_pivot[col] = 0

        for col in ["外資", "投信", "自營商"]:
            df_pivot[col] = np.round(df_pivot[col]).astype(int)

        df_pivot["合計"] = (
            df_pivot["外資"] + df_pivot["投信"] + df_pivot["自營商"]
        )

        df_result = df_pivot.tail(7).iloc[::-1].reset_index()
        df_result.rename(columns={"date": "日期"}, inplace=True)
        df_result["日期"] = pd.to_datetime(df_result["日期"]).dt.strftime(
            "%m/%d"
        )

        return df_result[["日期", "外資", "投信", "自營商", "合計"]]

    except Exception as e:
        return pd.DataFrame(
            [["網路異常", 0, 0, 0, 0]],
            columns=["日期", "外資", "投信", "自營商", "合計"],
        )


# ---------------------------------------------------------
# 日 K 即時資料合併核心 Logic (修復盤中日 K 即時更新)
# ---------------------------------------------------------
def merge_realtime_to_daily(df_daily, realtime_data):
    if df_daily.empty or not realtime_data:
        return df_daily

    df_res = df_daily.copy()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    price = (
        realtime_data.get("price")
        or realtime_data.get("close")
        or realtime_data.get("last_price")
        or realtime_data.get("z")
    )
    high = (
        realtime_data.get("high")
        or realtime_data.get("h")
        or realtime_data.get("max")
        or price
    )
    low = (
        realtime_data.get("low")
        or realtime_data.get("l")
        or realtime_data.get("min")
        or price
    )
    open_price = (
        realtime_data.get("open") or realtime_data.get("o") or price
    )
    volume = (
        realtime_data.get("volume")
        or realtime_data.get("v")
        or realtime_data.get("tv")
        or 0
    )

    if price is None:
        return df_res

    price = float(price)
    high = float(high) if high is not None else price
    low = float(low) if low is not None else price
    open_price = float(open_price) if open_price is not None else price
    volume = int(volume)

    last_date = str(df_res.iloc[-1]["DateStr"])

    if last_date == today_str:
        idx = df_res.index[-1]
        df_res.loc[idx, "High"] = max(df_res.loc[idx, "High"], high)
        df_res.loc[idx, "Low"] = min(df_res.loc[idx, "Low"], low)
        df_res.loc[idx, "Close"] = price
        if volume > 0:
            df_res.loc[idx, "Volume"] = volume
    else:
        new_row = {
            "DateStr": today_str,
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": price,
            "Volume": volume,
        }
        df_res = pd.concat([df_res, pd.DataFrame([new_row])], ignore_index=True)

    print(f"[日 K 即時動態合併成功] 最新成交價={price}, 張數={volume}")
    return df_res


# ---------------------------------------------------------
# 3. 技術指標計算 (含吸拉派落與莊家控盤)
# ---------------------------------------------------------
def ema_func(series, period):
    return series.ewm(span=period, adjust=False, min_periods=0).mean()


def calculate_custom_indicators(df):
    if df.empty or len(df) < 20:
        return df

    # === 吸拉派落計算 ===
    close = df["Close"]
    ema13_1 = ema_func(close, 13)
    vara = ema_func(ema13_1, 13)
    df["VARA"] = vara
    vara_prev = vara.shift(1)

    kp = (vara - vara_prev) / vara_prev * 1000
    df["KP"] = kp
    mm = kp.shift(1)
    df["MM"] = mm

    df["派"] = kp
    df["落"] = np.where(kp < 0, kp, np.nan)
    df["吸"] = np.where(kp >= mm, kp, np.nan)
    df["拉"] = np.where((kp >= 0) & (kp >= mm), kp, np.nan)

    # === 黃色多頭帶計算 ===
    df["JJ"] = (df["Close"] + df["High"] + df["Low"]) / 3
    df["E"] = df["JJ"].ewm(span=5, adjust=False).mean()
    df["D"] = df["E"].shift(1)
    df["E_gt_D"] = df["E"] > df["D"]

    # === 基礎均線與 MACD ===
    df["工作線"] = df["Close"].ewm(span=5, adjust=False).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    # 通達信: 買入:=CROSS(工作線,生命線);
    df["CROSS_GOLDEN"] = (df["工作線"] > df["MA20"]) & (
        df["工作線"].shift(1) <= df["MA20"].shift(1)
    )

    ema8 = df["Close"].ewm(span=8, adjust=False).mean()
    ema13 = df["Close"].ewm(span=13, adjust=False).mean()
    df["DIF"] = ema8 - ema13
    df["MACD"] = df["DIF"].ewm(span=5, adjust=False).mean()
    df["MACD_Hist"] = (df["DIF"] - df["MACD"]) * 2

    # === 趨勢紅綠線與買賣訊號 ===
    zyg28 = df["Close"]
    zyg_sma1 = zyg28.ewm(alpha=1 / 2, adjust=False).mean()
    zyg_sma2 = zyg_sma1.ewm(alpha=1 / 2, adjust=False).mean()
    df["ZYG29"] = zyg_sma2.ewm(alpha=1 / 2, adjust=False).mean()
    df["ZYG30"] = df["ZYG29"].rolling(window=3).mean()
    df["ZYG_Red"] = np.where(df["ZYG29"] > df["ZYG30"], df["ZYG29"], None)
    df["ZYG_Green"] = np.where(df["ZYG29"] <= df["ZYG30"], df["ZYG29"], None)

    df["ZYG_CROSS_BUY"] = (df["ZYG29"] > df["ZYG30"]) & (
        df["ZYG29"].shift(1) <= df["ZYG30"].shift(1)
    )
    vol_ma5 = df["Volume"].rolling(window=5).mean()
    df["V_UP"] = df["Volume"] > (vol_ma5 * 1.3)
    df["TREND_OK"] = df["Close"] > df["MA20"]
    df["REF_HHV10"] = df["High"].shift(1).rolling(window=10).max()
    df["BREAK_BOX"] = df["Close"] > df["REF_HHV10"]

    df["HIGH_WIN_BUY"] = (
        df["ZYG_CROSS_BUY"] & df["V_UP"] & df["TREND_OK"] & df["BREAK_BOX"]
    )
    df["BASE_GD"] = df["ZYG_CROSS_BUY"] & (~df["HIGH_WIN_BUY"])
    df["SELL_ALL"] = (df["ZYG29"] <= df["ZYG30"]) & (
        df["ZYG29"].shift(1) > df["ZYG30"].shift(1)
    )

    df["Signal_Text"] = None
    df["Signal_Color"] = None
    for i in range(len(df)):
        if df.iloc[i]["HIGH_WIN_BUY"]:
            df.iat[i, df.columns.get_loc("Signal_Text")] = "突破"
            df.iat[i, df.columns.get_loc("Signal_Color")] = "#FF00FF"
        elif df.iloc[i]["BASE_GD"]:
            df.iat[i, df.columns.get_loc("Signal_Text")] = "轉折"
            df.iat[i, df.columns.get_loc("Signal_Color")] = "#FFD700"

    # === 量能主力線 ===
    df["主力啟動線"] = df["Volume"].rolling(5).mean()
    df["主力洗盤線"] = df["Volume"].rolling(35).mean()
    df["資金異動線"] = df["Volume"].rolling(120).mean()

    cross_start_fund = (df["主力啟動線"] > df["資金異動線"]) & (
        df["主力啟動線"].shift(1) <= df["資金異動線"].shift(1)
    )
    cross_start_wash = (df["主力啟動線"] > df["主力洗盤線"]) & (
        df["主力啟動線"].shift(1) <= df["主力洗盤線"].shift(1)
    )
    df["VOL_出擊"] = cross_start_fund | (
        (df["主力洗盤線"] > df["資金異動線"]) & cross_start_wash
    )

    cross_vol_start = (df["Volume"] > df["主力啟動線"]) & (
        df["Volume"].shift(1) <= df["主力啟動線"].shift(1)
    )
    ref_vol_low = (df["Volume"].shift(1) < df["資金異動線"].shift(1)) | (
        df["Volume"].shift(2) < df["資金異動線"].shift(2)
    )
    df["VOL_啟動"] = (
        (df["主力啟動線"] > df["主力啟動線"].shift(1))
        & cross_vol_start
        & ref_vol_low
    )

    df["V1"] = (df["Close"] / df["Close"].shift(3)) >= 1.10
    v1_forward = df["V1"].shift(-1).fillna(False)
    df["VOL_OK"] = df["V1"] | v1_forward

    # === 莊家控盤 ===
    var1_ema1 = df["Close"].ewm(span=9, adjust=False).mean()
    var1 = var1_ema1.ewm(span=9, adjust=False).mean()

    var1_ref1 = var1.shift(1)
    df["控盤"] = np.where(
        var1_ref1 != 0, (var1 - var1_ref1) / var1_ref1 * 1000, 0
    )
    df["控盤_REF"] = df["控盤"].shift(1)

    df["AA0"] = (df["控盤"] > 0) & (df["控盤_REF"] <= 0)
    df["開始控盤"] = np.where(df["AA0"], 5.0, 0.0)

    low_60 = df["Low"].rolling(window=60, min_periods=1).min()
    high_60 = df["High"].rolling(window=60, min_periods=1).max()
    price_range = np.where((high_60 - low_60) == 0, 1, high_60 - low_60)

    winner_95 = np.clip(
        (df["Close"] * 0.95 - low_60) / price_range * 100, 0, 100
    )
    cost_85 = low_60 + price_range * 0.85

    df["無莊控盤"] = df["控盤"] < 0
    df["有莊控盤"] = (df["控盤"] > df["控盤_REF"]) & (df["控盤"] > 0)
    df["高度控盤"] = (
        (winner_95 > 50) & (df["Close"] > cost_85) & (df["控盤"] > 0)
    )
    df["主力出貨"] = (df["控盤"] < df["控盤_REF"]) & (df["控盤"] > 0)

    return df


# ---------------------------------------------------------
# 4A. ECharts 60分鐘K渲染器
# ---------------------------------------------------------
def render_echarts_html_60(df, height=950):
    if df is None or df.empty:
        return "<div style='color:white;padding:20px;'>沒有60分鐘K資料</div>"

    df = df.copy()

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"]).reset_index(
        drop=True
    )

    if df.empty:
        return "<div style='color:white;padding:20px;'>60分鐘K資料無效</div>"

    # 1. 均線與量能主力線
    df["EMA5_60"] = df["Close"].ewm(span=5, adjust=False).mean()
    df["MA10_60"] = df["Close"].rolling(window=10, min_periods=1).mean()
    df["MA20_60"] = df["Close"].rolling(window=20, min_periods=1).mean()
    df["MA60_60"] = df["Close"].rolling(window=60, min_periods=1).mean()

    df["CROSS_GOLDEN_60"] = (df["EMA5_60"] > df["MA20_60"]) & (
        df["EMA5_60"].shift(1) <= df["MA20_60"].shift(1)
    )

    df["主力啟動線_60"] = df["Volume"].rolling(window=5, min_periods=1).mean()
    df["主力洗盤線_60"] = df["Volume"].rolling(window=35, min_periods=1).mean()
    df["資金異動線_60"] = (
        df["Volume"].rolling(window=120, min_periods=1).mean()
    )

    df["VOL_OK_60"] = df["Volume"] > df["主力啟動線_60"] * 1.3
    df["VOL_出擊_60"] = df["主力啟動線_60"] > df["主力洗盤線_60"]
    df["VOL_啟動_60"] = (df["Volume"] > df["主力啟動線_60"]) & (
        df["主力啟動線_60"] > df["主力啟動線_60"].shift(1)
    )

    # 2. 黃色多頭帶
    df["JJ_60"] = (df["Close"] + df["High"] + df["Low"]) / 3
    df["E_60"] = df["JJ_60"].ewm(span=5, adjust=False).mean()
    df["D_60"] = df["E_60"].shift(1)
    df["E_gt_D_60"] = df["E_60"] > df["D_60"]

    # 3. ZYG 趨勢線
    zyg28_60 = df["Close"]
    zyg_sma1_60 = zyg28_60.ewm(alpha=1 / 2, adjust=False).mean()
    zyg_sma2_60 = zyg_sma1_60.ewm(alpha=1 / 2, adjust=False).mean()
    df["ZYG29_60"] = zyg_sma2_60.ewm(alpha=1 / 2, adjust=False).mean()
    df["ZYG30_60"] = df["ZYG29_60"].rolling(window=3, min_periods=1).mean()

    df["ZYG_Red_60"] = np.where(
        df["ZYG29_60"] > df["ZYG30_60"], df["ZYG29_60"], np.nan
    )
    df["ZYG_Green_60"] = np.where(
        df["ZYG29_60"] <= df["ZYG30_60"], df["ZYG29_60"], np.nan
    )

    df["ZYG_CROSS_BUY_60"] = (df["ZYG29_60"] > df["ZYG30_60"]) & (
        df["ZYG29_60"].shift(1) <= df["ZYG30_60"].shift(1)
    )
    df["ZYG_SELL_60"] = (df["ZYG29_60"] <= df["ZYG30_60"]) & (
        df["ZYG29_60"].shift(1) > df["ZYG30_60"].shift(1)
    )

    # 4. 突破訊號
    vol_ma5_60 = df["Volume"].rolling(window=5, min_periods=1).mean()
    df["V_UP_60"] = df["Volume"] > vol_ma5_60 * 1.3
    df["TREND_OK_60"] = df["Close"] > df["MA20_60"]
    df["REF_HHV10_60"] = (
        df["High"].shift(1).rolling(window=10, min_periods=1).max()
    )
    df["BREAK_BOX_60"] = df["Close"] > df["REF_HHV10_60"]

    df["HIGH_WIN_BUY_60"] = (
        df["ZYG_CROSS_BUY_60"]
        & df["V_UP_60"]
        & df["TREND_OK_60"]
        & df["BREAK_BOX_60"]
    )
    df["BASE_GD_60"] = df["ZYG_CROSS_BUY_60"] & (~df["HIGH_WIN_BUY_60"])

    # 5. 「莊家抬轎」指標計算 (60分K)
    var1_ema1_60 = df["Close"].ewm(span=9, adjust=False).mean()
    var1_60 = var1_ema1_60.ewm(span=9, adjust=False).mean()
    var1_ref1_60 = var1_60.shift(1)
    df["控盤_60"] = np.where(
        var1_ref1_60 != 0, (var1_60 - var1_ref1_60) / var1_ref1_60 * 1000, 0
    )
    df["控盤_REF_60"] = df["控盤_60"].shift(1)

    df["AA0_60"] = (df["控盤_60"] > 0) & (df["控盤_REF_60"] <= 0)
    df["開始控盤_60"] = np.where(df["AA0_60"], 5.0, 0.0)

    low_60 = df["Low"].rolling(window=60, min_periods=1).min()
    high_60 = df["High"].rolling(window=60, min_periods=1).max()
    price_range_60 = np.where((high_60 - low_60) == 0, 1, high_60 - low_60)

    winner_95_60 = np.clip(
        (df["Close"] * 0.95 - low_60) / price_range_60 * 100, 0, 100
    )
    cost_85_60 = low_60 + price_range_60 * 0.85

    df["高度控盤_60"] = (
        (winner_95_60 > 50) & (df["Close"] > cost_85_60) & (df["控盤_60"] > 0)
    )
    df["有莊控盤_60"] = (df["控盤_60"] > df["控盤_REF_60"]) & (
        df["控盤_60"] > 0
    )
    df["主力出貨_60"] = (df["控盤_60"] < df["控盤_REF_60"]) & (
        df["控盤_60"] > 0
    )

    # 6. 「吸拉派落」指標計算 (60分K)
    ema13_60 = df["Close"].ewm(span=13, adjust=False).mean()
    vara_60 = ema13_60.ewm(span=13, adjust=False).mean()
    vara_prev_60 = vara_60.shift(1)

    kp_60 = (vara_60 - vara_prev_60) / vara_prev_60 * 1000
    df["KP_60"] = kp_60
    mm_60 = kp_60.shift(1)
    df["MM_60"] = mm_60

    df["派_60"] = kp_60
    df["落_60"] = np.where(kp_60 < 0, kp_60, np.nan)
    df["吸_60"] = np.where(kp_60 >= mm_60, kp_60, np.nan)
    df["拉_60"] = np.where((kp_60 >= 0) & (kp_60 >= mm_60), kp_60, np.nan)

    # 7. MACD 正確計算 (8, 13, 5)
    ema8_macd = df["Close"].ewm(span=8, adjust=False).mean()
    ema13_macd = df["Close"].ewm(span=13, adjust=False).mean()
    df["DIF_60"] = ema8_macd - ema13_macd
    df["DEA_60"] = df["DIF_60"].ewm(span=5, adjust=False).mean()
    df["MACD_Hist_60"] = (df["DIF_60"] - df["DEA_60"]) * 2

    macd_data = [
        {
            "value": round(float(x), 2) if pd.notna(x) else 0,
            "itemStyle": {
                "color": "#FF3333" if (pd.notna(x) and x >= 0) else "#00AA00"
            },
        }
        for x in df["MACD_Hist_60"]
    ]

    # 8. 基礎數據轉換 (含金叉白柱渲染)
    dates = df["DateStr"].astype(str).tolist()

    k_values = []
    for _, row in df.iterrows():
        open_val = round(float(row["Open"]), 2)
        close_val = round(float(row["Close"]), 2)
        low_val = round(float(row["Low"]), 2)
        high_val = round(float(row["High"]), 2)

        if row.get("CROSS_GOLDEN_60", False):
            k_values.append({
                "value": [open_val, close_val, low_val, high_val],
                "itemStyle": {
                    "color": "#FFFFFF",
                    "color0": "#FFFFFF",
                    "borderColor": "#FFFFFF",
                    "borderColor0": "#FFFFFF",
                },
            })
        else:
            k_values.append([open_val, close_val, low_val, high_val])

    def clean_list(series):
        return [None if pd.isna(x) else round(float(x), 2) for x in series]

    yellow_bar_data = []
    for _, row in df.iterrows():
        if row["E_gt_D_60"] and pd.notna(row["D_60"]):
            d_val = round(float(row["D_60"]), 2)
            e_val = round(float(row["E_60"]), 2)
            yellow_bar_data.append([d_val, e_val, d_val, e_val])
        else:
            yellow_bar_data.append([None, None, None, None])

    mark_points = []
    for idx, row in df.iterrows():
        if bool(row["HIGH_WIN_BUY_60"]):
            mark_points.append({
                "name": "突破",
                "coord": [str(row["DateStr"]), float(row["Low"])],
                "value": "突破",
                "symbol": "arrow",
                "symbolSize": 10,
                "itemStyle": {"color": "#FF00FF"},
                "label": {
                    "position": "bottom",
                    "distance": 5,
                    "fontSize": 11,
                    "color": "#FF00FF",
                },
            })
        elif bool(row["BASE_GD_60"]):
            mark_points.append({
                "name": "轉折",
                "coord": [str(row["DateStr"]), float(row["Low"])],
                "value": "轉折",
                "symbol": "arrow",
                "symbolSize": 8,
                "itemStyle": {"color": "#FFD700"},
                "label": {
                    "position": "bottom",
                    "distance": 5,
                    "fontSize": 11,
                    "color": "#FFD700",
                },
            })
        if bool(row["ZYG_SELL_60"]):
            mark_points.append({
                "name": "賣點",
                "coord": [str(row["DateStr"]), float(row["High"])],
                "value": "賣點",
                "symbol": "arrow",
                "symbolSize": 8,
                "symbolRotate": 180,
                "itemStyle": {"color": "#00FF00"},
                "label": {
                    "position": "top",
                    "distance": 5,
                    "fontSize": 11,
                    "color": "#00FF00",
                },
            })

    volume_data = []
    vol_white_line_data = []
    for _, row in df.iterrows():
        vol_val = int(row["Volume"])
        if row["VOL_OK_60"]:
            color = "#FF0033"
        elif row["VOL_出擊_60"]:
            color = "#FFFF00"
        elif row["VOL_啟動_60"]:
            color = "#00FF00"
        else:
            color = "#CC2222" if row["Close"] >= row["Open"] else "#00AA00"

        volume_data.append({"value": vol_val, "itemStyle": {"color": color}})
        vol_white_line_data.append(vol_val if row["VOL_OK_60"] else None)

    # 莊家抬轎 Series 資料
    zhuang_data_60 = []
    kaishi_line_data_60 = []
    for idx, row in df.iterrows():
        val = round(float(row["控盤_60"]), 2) if pd.notna(row["控盤_60"]) else 0
        if row["高度控盤_60"]:
            color = "#FF00FF"
        elif row["有莊控盤_60"]:
            color = "#FF3333"
        elif row["主力出貨_60"]:
            color = "#00FF00"
        else:
            color = "#FFFFFF"

        zhuang_data_60.append({"value": val, "itemStyle": {"color": color}})
        kaishi_line_data_60.append(5.0 if row["AA0_60"] else 0.0)

    total_len = len(dates)
    start_percent = int((1 - 70 / total_len) * 100) if total_len > 70 else 0

    # 9. ECharts 5-Grid 配置
    options = {
        "backgroundColor": "#131722",
        "animation": False,
        "tooltip": {"show": False},  # 已關閉手機觸控浮窗
        "grid": [
            {"left": "4%", "right": "3%", "top": "2%", "height": "36%"},
            {"left": "4%", "right": "3%", "top": "40%", "height": "11%"},
            {"left": "4%", "right": "3%", "top": "53%", "height": "10%"},
            {"left": "4%", "right": "3%", "top": "65%", "height": "10%"},
            {"left": "4%", "right": "3%", "top": "77%", "height": "12%"},
        ],
        "xAxis": [
            {
                "type": "category",
                "data": dates,
                "gridIndex": 0,
                "axisLabel": {"show": True},
            },
            {
                "type": "category",
                "data": dates,
                "gridIndex": 1,
                "axisLabel": {"show": False},
            },
            {
                "type": "category",
                "data": dates,
                "gridIndex": 2,
                "axisLabel": {"show": False},
            },
            {
                "type": "category",
                "data": dates,
                "gridIndex": 3,
                "axisLabel": {"show": False},
            },
            {
                "type": "category",
                "data": dates,
                "gridIndex": 4,
                "axisLabel": {"show": False},
            },
        ],
        "yAxis": [
            {"scale": True, "gridIndex": 0},
            {"scale": True, "gridIndex": 1},
            {"scale": True, "gridIndex": 2},
            {
                "scale": True,
                "gridIndex": 3,
                "splitLine": {"show": True, "lineStyle": {"color": "#2A2E39"}},
            },
            {
                "scale": True,
                "gridIndex": 4,
                "splitLine": {"show": True, "lineStyle": {"color": "#2A2E39"}},
            },
        ],
        "dataZoom": [
            {
                "type": "inside",
                "xAxisIndex": [0, 1, 2, 3, 4],
                "start": start_percent,
                "end": 100,
            },
            {
                "type": "slider",
                "xAxisIndex": [0, 1, 2, 3, 4],
                "start": start_percent,
                "end": 100,
                "bottom": "1%",
            },
        ],
        "series": [
            {
                "name": "黃色多頭帶",
                "type": "candlestick",
                "data": yellow_bar_data,
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "z": 1,
                "itemStyle": {
                    "color": "#FFFF00",
                    "color0": "#FFFF00",
                    "borderColor": "#FFFF00",
                    "borderColor0": "#FFFF00",
                },
            },
            {
                "name": "60分K",
                "type": "candlestick",
                "data": k_values,
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "z": 2,
                "itemStyle": {
                    "color": "#FF3333",
                    "color0": "#00AA00",
                    "borderColor": "#FF3333",
                    "borderColor0": "#00AA00",
                },
                "markPoint": {"data": mark_points},
            },
            {
                "name": "EMA5",
                "type": "line",
                "data": clean_list(df["EMA5_60"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFFFF", "width": 1},
            },
            {
                "name": "MA10",
                "type": "line",
                "data": clean_list(df["MA10_60"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFF00", "width": 1},
            },
            {
                "name": "MA20",
                "type": "line",
                "data": clean_list(df["MA20_60"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#FF1493", "width": 1},
            },
            {
                "name": "MA60",
                "type": "line",
                "data": clean_list(df["MA60_60"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#00FFFF", "width": 1},
            },
            {
                "name": "趨勢紅線",
                "type": "line",
                "data": clean_list(df["ZYG_Red_60"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "connectNulls": False,
                "lineStyle": {"color": "#FF0055", "width": 3},
            },
            {
                "name": "趨勢綠線",
                "type": "line",
                "data": clean_list(df["ZYG_Green_60"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "connectNulls": False,
                "lineStyle": {"color": "#00FF66", "width": 3},
            },
            {
                "name": "成交量",
                "type": "bar",
                "data": volume_data,
                "xAxisIndex": 1,
                "yAxisIndex": 1,
            },
            {
                "name": "OK白線",
                "type": "bar",
                "data": vol_white_line_data,
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "barWidth": 6,
                "barGap": "-100%",
                "z": 10,
                "itemStyle": {"color": "#FFFFFF"},
            },
            {
                "name": "主力啟動線(5)",
                "type": "line",
                "data": clean_list(df["主力啟動線_60"]),
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFFFF", "width": 1},
            },
            {
                "name": "主力洗盤線(35)",
                "type": "line",
                "data": clean_list(df["主力洗盤線_60"]),
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFF00", "width": 1},
            },
            {
                "name": "資金異動線(120)",
                "type": "line",
                "data": clean_list(df["資金異動線_60"]),
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "showSymbol": False,
                "lineStyle": {"color": "#00FF00", "width": 1},
            },
            {
                "name": "MACD",
                "type": "bar",
                "data": macd_data,
                "xAxisIndex": 2,
                "yAxisIndex": 2,
            },
            {
                "name": "DIF",
                "type": "line",
                "data": clean_list(df["DIF_60"]),
                "xAxisIndex": 2,
                "yAxisIndex": 2,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFFFF", "width": 1},
            },
            {
                "name": "DEA",
                "type": "line",
                "data": clean_list(df["DEA_60"]),
                "xAxisIndex": 2,
                "yAxisIndex": 2,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFF00", "width": 1},
            },
            {
                "name": "莊家控盤",
                "type": "bar",
                "data": zhuang_data_60,
                "xAxisIndex": 3,
                "yAxisIndex": 3,
            },
            {
                "name": "開始控盤",
                "type": "line",
                "data": kaishi_line_data_60,
                "xAxisIndex": 3,
                "yAxisIndex": 3,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFF00", "width": 2},
            },
            {
                "name": "MM",
                "type": "line",
                "data": clean_list(df["MM_60"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#888888", "width": 1, "type": "dashed"},
            },
            {
                "name": "派",
                "type": "line",
                "data": clean_list(df["派_60"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#00FF00", "width": 2},
            },
            {
                "name": "落",
                "type": "line",
                "data": clean_list(df["落_60"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFFFF", "width": 2},
            },
            {
                "name": "吸",
                "type": "line",
                "data": clean_list(df["吸_60"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#F08080", "width": 2},
            },
            {
                "name": "拉",
                "type": "line",
                "data": clean_list(df["拉_60"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#FF0000", "width": 2},
            },
        ],
    }

    options_json = json.dumps(options, ensure_ascii=False)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
        <style>
            html, body {{
                margin: 0;
                padding: 0;
                width: 100%;
                height: 100%;
                background-color: #131722;
            }}
            #main {{
                width: 100%;
                height: {height}px;
            }}
        </style>
    </head>
    <body>
        <div id="main"></div>
        <script>
            var chartDom = document.getElementById("main");
            var myChart = echarts.init(chartDom, "dark");
            var option = {options_json};
            myChart.setOption(option);
            window.addEventListener("resize", function() {{
                myChart.resize();
            }});
        </script>
    </body>
    </html>
    """
    return html


# ---------------------------------------------------------
# 4B. ECharts 日 K 渲染器
# ---------------------------------------------------------
def render_echarts_html(df, height=950):
    dates = df["DateStr"].tolist()

    k_values = []
    for _, row in df.iterrows():
        open_val = float(row["Open"])
        close_val = float(row["Close"])
        low_val = float(row["Low"])
        high_val = float(row["High"])

        if row.get("CROSS_GOLDEN", False):
            k_values.append({
                "value": [open_val, close_val, low_val, high_val],
                "itemStyle": {
                    "color": "#FFFFFF",
                    "color0": "#FFFFFF",
                    "borderColor": "#FFFFFF",
                    "borderColor0": "#FFFFFF",
                },
            })
        else:
            k_values.append([open_val, close_val, low_val, high_val])

    yellow_bar_data = []
    for _, row in df.iterrows():
        if row["E_gt_D"] and pd.notna(row["D"]):
            d_val = round(float(row["D"]), 2)
            e_val = round(float(row["E"]), 2)
            yellow_bar_data.append([d_val, e_val, d_val, e_val])
        else:
            yellow_bar_data.append([None, None, None, None])

    mark_points = []
    for idx, row in df.iterrows():
        if pd.notna(row["Signal_Text"]):
            mark_points.append({
                "name": str(row["Signal_Text"]),
                "coord": [str(row["DateStr"]), float(row["Low"])],
                "value": str(row["Signal_Text"]),
                "symbol": "arrow",
                "symbolSize": 8,
                "itemStyle": {"color": str(row["Signal_Color"])},
                "label": {
                    "position": "bottom",
                    "distance": 5,
                    "fontSize": 11,
                    "color": str(row["Signal_Color"]),
                },
            })
        if row["SELL_ALL"]:
            mark_points.append({
                "name": "賣點",
                "coord": [str(row["DateStr"]), float(row["High"])],
                "value": "賣點",
                "symbol": "arrow",
                "symbolSize": 8,
                "symbolRotate": 180,
                "itemStyle": {"color": "#00FF00"},
                "label": {
                    "position": "top",
                    "distance": 5,
                    "fontSize": 11,
                    "color": "#00FF00",
                },
            })

    vol_base_data = []
    vol_white_line_data = []

    for _, row in df.iterrows():
        vol_val = int(row["Volume"])

        if row["VOL_OK"]:
            color = "#FF0033"
        elif row["VOL_出擊"]:
            color = "#FFFF00"
        elif row["VOL_啟動"]:
            color = "#00FF00"
        else:
            color = "#CC2222" if row["Close"] >= row["Open"] else "#00AA00"

        vol_base_data.append({"value": vol_val, "itemStyle": {"color": color}})
        vol_white_line_data.append(vol_val if row["VOL_OK"] else None)

    macd_data = [
        {
            "value": round(float(row["MACD_Hist"]), 2),
            "itemStyle": {
                "color": "#FF3333" if row["MACD_Hist"] >= 0 else "#00AA00"
            },
        }
        for _, row in df.iterrows()
    ]

    zhuang_data = []
    kaishi_line_data = []

    for idx, row in df.iterrows():
        val = round(float(row["控盤"]), 2) if pd.notna(row["控盤"]) else 0
        if row["高度控盤"]:
            color = "#FF00FF"
        elif row["有莊控盤"]:
            color = "#FF3333"
        elif row["主力出貨"]:
            color = "#00FF00"
        else:
            color = "#FFFFFF"

        zhuang_data.append({"value": val, "itemStyle": {"color": color}})
        kaishi_line_data.append(5.0 if row["AA0"] else 0.0)

    total_len = len(dates)
    start_percent = (
        max(0, int((1 - 70 / total_len) * 100)) if total_len > 70 else 0
    )

    def clean_list(series):
        return [None if pd.isna(x) else round(float(x), 2) for x in series]

    options = {
        "backgroundColor": "#131722",
        "animation": False,
        "tooltip": {"show": False},  # 已關閉手機觸控浮窗
        "grid": [
            {"left": "4%", "right": "3%", "top": "2%", "height": "36%"},
            {"left": "4%", "right": "3%", "top": "40%", "height": "11%"},
            {"left": "4%", "right": "3%", "top": "53%", "height": "10%"},
            {"left": "4%", "right": "3%", "top": "65%", "height": "10%"},
            {"left": "4%", "right": "3%", "top": "77%", "height": "12%"},
        ],
        "xAxis": [
            {"type": "category", "data": dates, "gridIndex": 0},
            {
                "type": "category",
                "data": dates,
                "gridIndex": 1,
                "axisLabel": {"show": False},
            },
            {
                "type": "category",
                "data": dates,
                "gridIndex": 2,
                "axisLabel": {"show": False},
            },
            {
                "type": "category",
                "data": dates,
                "gridIndex": 3,
                "axisLabel": {"show": False},
            },
            {
                "type": "category",
                "data": dates,
                "gridIndex": 4,
                "axisLabel": {"show": False},
            },
        ],
        "yAxis": [
            {"scale": True, "gridIndex": 0},
            {"scale": True, "gridIndex": 1},
            {"scale": True, "gridIndex": 2},
            {
                "scale": True,
                "gridIndex": 3,
                "splitLine": {"show": True, "lineStyle": {"color": "#2A2E39"}},
            },
            {
                "scale": True,
                "gridIndex": 4,
                "splitLine": {"show": True, "lineStyle": {"color": "#2A2E39"}},
            },
        ],
        "dataZoom": [
            {
                "type": "inside",
                "xAxisIndex": [0, 1, 2, 3, 4],
                "start": start_percent,
                "end": 100,
            },
            {
                "type": "slider",
                "xAxisIndex": [0, 1, 2, 3, 4],
                "start": start_percent,
                "end": 100,
                "bottom": "1%",
            },
        ],
        "series": [
            {
                "name": "黃色多頭帶",
                "type": "candlestick",
                "data": yellow_bar_data,
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "z": 1,
                "itemStyle": {
                    "color": "#FFFF00",
                    "color0": "#FFFF00",
                    "borderColor": "#FFFF00",
                    "borderColor0": "#FFFF00",
                },
            },
            {
                "name": "日K",
                "type": "candlestick",
                "data": k_values,
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "z": 2,
                "itemStyle": {
                    "color": "#FF3333",
                    "color0": "#00AA00",
                    "borderColor": "#FF3333",
                    "borderColor0": "#00AA00",
                },
                "markPoint": {"data": mark_points},
            },
            {
                "name": "EMA5",
                "type": "line",
                "data": clean_list(df["工作線"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFFFF", "width": 1},
            },
            {
                "name": "MA10",
                "type": "line",
                "data": clean_list(df["MA10"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFF00", "width": 1},
            },
            {
                "name": "MA20",
                "type": "line",
                "data": clean_list(df["MA20"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#FF1493", "width": 1},
            },
            {
                "name": "MA60",
                "type": "line",
                "data": clean_list(df["MA60"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#00FFFF", "width": 1},
            },
            {
                "name": "趨勢紅線",
                "type": "line",
                "data": clean_list(df["ZYG_Red"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#FF0055", "width": 3},
            },
            {
                "name": "趨勢綠線",
                "type": "line",
                "data": clean_list(df["ZYG_Green"]),
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "showSymbol": False,
                "lineStyle": {"color": "#00FF66", "width": 3},
            },
            {
                "name": "成交量",
                "type": "bar",
                "data": vol_base_data,
                "xAxisIndex": 1,
                "yAxisIndex": 1,
            },
            {
                "name": "OK白線",
                "type": "bar",
                "data": vol_white_line_data,
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "barWidth": 6,
                "barGap": "-100%",
                "z": 10,
                "itemStyle": {"color": "#FFFFFF"},
            },
            {
                "name": "主力啟動線(5)",
                "type": "line",
                "data": clean_list(df["主力啟動線"]),
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFFFF", "width": 1},
            },
            {
                "name": "主力洗盤線(35)",
                "type": "line",
                "data": clean_list(df["主力洗盤線"]),
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFF00", "width": 1},
            },
            {
                "name": "資金異動線(120)",
                "type": "line",
                "data": clean_list(df["資金異動線"]),
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "showSymbol": False,
                "lineStyle": {"color": "#00FF00", "width": 1},
            },
            {
                "name": "MACD",
                "type": "bar",
                "data": macd_data,
                "xAxisIndex": 2,
                "yAxisIndex": 2,
            },
            {
                "name": "DIF",
                "type": "line",
                "data": clean_list(df["DIF"]),
                "xAxisIndex": 2,
                "yAxisIndex": 2,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFFFF", "width": 1},
            },
            {
                "name": "DEA",
                "type": "line",
                "data": clean_list(df["MACD"]),
                "xAxisIndex": 2,
                "yAxisIndex": 2,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFF00", "width": 1},
            },
            {
                "name": "莊家控盤",
                "type": "bar",
                "data": zhuang_data,
                "xAxisIndex": 3,
                "yAxisIndex": 3,
            },
            {
                "name": "開始控盤",
                "type": "line",
                "data": kaishi_line_data,
                "xAxisIndex": 3,
                "yAxisIndex": 3,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFF00", "width": 2},
            },
            {
                "name": "MM",
                "type": "line",
                "data": clean_list(df["MM"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#888888", "width": 1, "type": "dashed"},
            },
            {
                "name": "派",
                "type": "line",
                "data": clean_list(df["派"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#00FF00", "width": 2},
            },
            {
                "name": "落",
                "type": "line",
                "data": clean_list(df["落"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#FFFFFF", "width": 2},
            },
            {
                "name": "吸",
                "type": "line",
                "data": clean_list(df["吸"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#F08080", "width": 2},
            },
            {
                "name": "拉",
                "type": "line",
                "data": clean_list(df["拉"]),
                "xAxisIndex": 4,
                "yAxisIndex": 4,
                "showSymbol": False,
                "lineStyle": {"color": "#FF0000", "width": 2},
            },
        ],
    }

    options_json = json.dumps(options)
    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
        <style>
            html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; background-color: #131722; }}
            #main {{ width: 100%; height: {height}px; }}
        </style>
    </head>
    <body>
        <div id="main"></div>
        <script type="text/javascript">
            var chartDom = document.getElementById('main');
            var myChart = echarts.init(chartDom, 'dark');
            var option = {options_json};
            myChart.setOption(option);
            window.addEventListener('resize', function() {{ myChart.resize(); }});
        </script>
    </body>
    </html>
    """
    components.html(html_code, height=height + 10)


# ---------------------------------------------------------
# 5. 主畫面與側邊欄數據綁定 (日 K 即時動態寫回機制)
# ---------------------------------------------------------
# 直接呼叫，不要包在 with st.form 裡面
stock_code, submit_button = ma.render_search_bar(
    default_code="2330", key_prefix="top"
)

input_code = stock_code.strip()

# 側邊欄保留 K 線週期切換選項
kline_type = st.sidebar.radio(
    "K線週期", ["日K", "60分K"], horizontal=True, key="kline_type"
)

if input_code:
    stock_name, clean_code, df = fetch_stock_meta_and_kline(input_code)
    realtime = get_realtime_dde(clean_code)

    if not df.empty:
        df = (
            df.sort_values("DateStr")
            .drop_duplicates("DateStr", keep="last")
            .reset_index(drop=True)
        )
        df = merge_realtime_to_daily(df, realtime)
        df = calculate_custom_indicators(df)

    df_60 = fetch_60min_kline(clean_code)
    if not df_60.empty:
        df_60 = (
            df_60.sort_values("DateStr")
            .drop_duplicates("DateStr", keep="last")
            .reset_index(drop=True)
        )

    # ── 即時 API 診斷面板 (側邊欄) ──
    if isinstance(realtime, dict) and "price" in realtime:
        price = realtime.get("price", 0)
        prev_close = realtime.get("prev_close", 0)
        change = price - prev_close
        pct_change = (change / prev_close * 100) if prev_close else 0

        if change > 0:
            color = "#FF5252"
            arrow = "▲"
        elif change < 0:
            color = "#00E676"
            arrow = "▼"
        else:
            color = "#CCCCCC"
            arrow = ""

        diag_card_html = f"""
<div style="background-color: #1E222D; border: 1px solid #2A2E39; border-radius: 8px; padding: 12px; margin-bottom: 12px; color: #CCCCCC; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
    <div style="font-size: 14px; font-weight: 600; color: #8E94A2; margin-bottom: 4px;">🔍 即時 API 診斷</div>
    <div style="font-size: 13px; color: #9B9B9B;">{realtime.get('name', '')} ({realtime.get('code', '')})</div>
    <div style="font-size: 24px; font-weight: bold; color: {color}; margin: 2px 0;">{price:,.1f} <span style="font-size: 14px; font-weight: normal; color: #9B9B9B;">元</span></div>
    <div style="font-size: 14px; font-weight: 600; color: {color}; margin-bottom: 10px;">{arrow} {change:+.2f} ({pct_change:+.2f}%)</div>
    <div style="border-top: 1px solid #2A2E39; padding-top: 8px; font-size: 12px; display: grid; grid-template-columns: 1fr 1fr; row-gap: 4px;">
        <div>開盤：<span style="color: #FFFFFF;">{realtime.get('open', 0):,.1f}</span></div>
        <div>最高：<span style="color: #FFFFFF;">{realtime.get('high', 0):,.1f}</span></div>
        <div>最低：<span style="color: #FFFFFF;">{realtime.get('low', 0):,.1f}</span></div>
        <div>昨收：<span style="color: #FFFFFF;">{realtime.get('prev_close', 0):,.1f}</span></div>
        <div>總量：<span style="color: #FFFFFF;">{realtime.get('volume', 0):,} 張</span></div>
        <div>現張：<span style="color: #FFFFFF;">{realtime.get('single_vol', 0)} 張</span></div>
    </div>
</div>
"""
        st.sidebar.markdown(diag_card_html, unsafe_allow_html=True)
    else:
        st.sidebar.write(realtime)

    df_inst = fetch_institutional_data(clean_code)

    table_rows = ""
    for _, row in df_inst.iterrows():
        table_rows += "<tr style='border-bottom: 1px solid #2A2E39;'>"
        table_rows += (
            f"<td style='padding: 6px 2px; color: #CCCCCC;'>{row['日期']}</td>"
        )

        for col in ["外資", "投信", "自營商", "合計"]:
            val = row[col]
            if val > 0:
                color = "#FF3333"
                val_str = f"+{val}"
            elif val < 0:
                color = "#00FF66"
                val_str = str(val)
            else:
                color = "#888888"
                val_str = "0"

            weight = "bold" if col == "合計" else "normal"
            table_rows += f"<td style='padding: 6px 2px; text-align: right; color: {color}; font-weight: {weight};'>{val_str}</td>"
        table_rows += "</tr>"

    # ---------------------------------------------------------
    # 戰情多空共振燈號：5 分制多空對齊判定邏輯
    # ---------------------------------------------------------
    if not df.empty and len(df) >= 20:
        latest = df.iloc[-1]

        # === 多方 5 大條件判定 ===
        bull_c1 = latest["Close"] > latest["工作線"]
        bull_c2 = (
            (latest["ZYG29"] > latest["ZYG30"])
            or latest.get("HIGH_WIN_BUY", False)
            or latest.get("BASE_GD", False)
        )
        bull_c3 = (latest["MACD_Hist"] > 0) or (latest["DIF"] > latest["MACD"])
        bull_c4 = (latest["控盤"] > 0) or (latest["控盤"] > latest["控盤_REF"])
        bull_c5 = pd.notna(latest["KP"]) and (latest["KP"] >= 0)

        bull_score = sum([bull_c1, bull_c2, bull_c3, bull_c4, bull_c5])

        # === 空方 5 大條件判定 ===
        bear_c1 = latest["Close"] < latest["MA10"]
        bear_c2 = (latest["ZYG29"] <= latest["ZYG30"]) or latest.get(
            "SELL_ALL", False
        )
        bear_c3 = latest["DIF"] < latest["MACD"]
        bear_c4 = latest["控盤"] < 0
        bear_c5 = pd.notna(latest["KP"]) and (latest["KP"] < 0)

        bear_score = sum([bear_c1, bear_c2, bear_c3, bear_c4, bear_c5])

        # === 燈號等級歸類與 HTML 呈現 ===
        if bull_score == 5:
            light_html = "<span style='color: #FF3333; font-size: 15px;'>🔴 <b>【紅燈：準備數錢 / 買進】</b><br><span style='font-size: 11px; color: #CCCCCC;'>多方條件全面到齊 (5/5)，強烈共振！</span></span>"
            box_border = "#FF3333"
        elif bull_score == 4:
            light_html = "<span style='color: #FF66B2; font-size: 15px;'>🟠 <b>【粉燈：多頭看漲 / 準備】</b><br><span style='font-size: 11px; color: #CCCCCC;'>已集滿 4 項多方條件 (4/5)，偏多看待！</span></span>"
            box_border = "#FF66B2"
        elif bear_score == 5:
            light_html = "<span style='color: #00FF66; font-size: 15px;'>🟢 <b>【綠燈：賣點確立 / 閃人】</b><br><span style='font-size: 11px; color: #CCCCCC;'>空方條件全面到齊 (5/5)，極度危險！</span></span>"
            box_border = "#00FF66"
        elif bear_score == 4:
            light_html = "<span style='color: #FFFF00; font-size: 15px;'>🟡 <b>【黃燈：逐步下跌 / 警戒】</b><br><span style='font-size: 11px; color: #CCCCCC;'>已集滿 4 項空方條件 (4/5)，隨時聽牌轉綠！</span></span>"
            box_border = "#FFFF00"
        else:
            light_html = "<span style='color: #AAAAAA; font-size: 14px;'>⚪ <b>【灰燈：安靜抱股 / 觀望】</b><br><span style='font-size: 11px; color: #888888;'>多空均未達 4 項標準，拉鋸觀望。</span></span>"
            box_border = "#2A2E39"

        st.sidebar.markdown(
            f"""
                <div class="stock-info-card" style="border: 2px solid {box_border}; text-align: center; padding: 10px; margin-bottom: 10px;">
                    <div class="metric-title" style="margin-bottom: 5px;">🚦 戰情多空共振燈號</div>
                    <div style="padding: 4px 0;">{light_html}</div>
                </div>
            """,
            unsafe_allow_html=True,
        )

    html_table = f"""
    <div class="stock-info-card" style="padding: 8px 3px; overflow-x: hidden;">
        <div class="metric-title" style="margin-bottom: 8px;">📊 近 7 日三大法人買賣超 (張)</div>
        <table style="width: 100%; font-size: 11px; border-collapse: collapse; font-family: monospace;">
            <thead>
                <tr style="border-bottom: 1px solid #444444; color: #888888; text-align: right;">
                    <th style="text-align: left; padding: 4px 0px; white-space: nowrap;">日期</th>
                    <th style="padding: 4px 0px; white-space: nowrap;">外資</th>
                    <th style="padding: 4px 0px; white-space: nowrap;">投信</th>
                    <th style="padding: 4px 0px; white-space: nowrap;">自營</th>
                    <th style="padding: 4px 0px; white-space: nowrap;">合計</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
    </div>
    """
    st.sidebar.markdown(html_table, unsafe_allow_html=True)

    if not df.empty and len(df) >= 20:
        latest = df.iloc[-1]
        prev_close = df.iloc[-2]["Close"] if len(df) > 1 else latest["Close"]
        change = latest["Close"] - prev_close
        pct_change = (change / prev_close) * 100

        # ---------- 首頁雙層抬頭（手機雙排 / 桌機單排自適應） ----------
        metrics_dict = {
            "EMA5": f"{latest['工作線']:.2f}",
            "MA10": f"{latest['MA10']:.2f}",
            "MA20": f"{latest['MA20']:.2f}",
            "MA60": f"{latest['MA60']:.2f}",
            "成交量": f"{int(latest['Volume']):,}張",
            "控盤值": f"{latest['控盤']:.2f}",
        }

        header_html = ma.get_stock_header_html(
            stock_code=clean_code,
            stock_name=stock_name,
            close_price=latest["Close"],
            change=change,
            pct_change=pct_change,
            metrics_dict=metrics_dict,
            is_mobile=True,  # 啟用手機雙排修護卡片
        )
        st.markdown(header_html, unsafe_allow_html=True)
        # -------------------------------------------------------------------

        # ── 置頂卡片：🛡️ 實戰風控與關鍵關卡 ──
        render_risk_card(df)

        # --- 莊家控盤狀態 ---
        if latest["高度控盤"]:
            zhuang_status = "高度控盤"
            zhuang_color = "#FF00FF"
        elif latest["有莊控盤"]:
            zhuang_status = "有莊控盤"
            zhuang_color = "#FF3333"
        elif latest["主力出貨"]:
            zhuang_status = "主力出貨"
            zhuang_color = "#00FF00"
        else:
            zhuang_status = "無莊控盤"
            zhuang_color = "#FFFFFF"

        st.sidebar.markdown(
            f"""
                <div class="stock-info-card">
                    <div class="metric-title">🎯 莊家抬轎指標</div>
                    <div class="metric-row"><span class="metric-label">控盤值:</span><span class="metric-value">{latest['控盤']:.2f}</span></div>
                    <div class="metric-row"><span class="metric-label">當前狀態:</span><span class="metric-value" style="color:{zhuang_color};">{zhuang_status}</span></div>
                </div>
            """,
            unsafe_allow_html=True,
        )

        # --- 量能主力訊號 ---
        if latest["VOL_OK"]:
            vol_sig_text = "暴漲 OK"
            vol_sig_color = "#FF0033"
        elif latest["VOL_出擊"]:
            vol_sig_text = "主力出擊"
            vol_sig_color = "#FFFF00"
        elif latest["VOL_啟動"]:
            vol_sig_text = "低位啟動"
            vol_sig_color = "#00FF00"
        else:
            vol_sig_text = "一般量能"
            vol_sig_color = "#888888"

        st.sidebar.markdown(
            f"""
                <div class="stock-info-card">
                    <div class="metric-title">🔥 量能主力訊號</div>
                    <div class="metric-row"><span class="metric-label">主力啟動線(5):</span><span class="metric-value">{int(latest['主力啟動線']):,}</span></div>
                    <div class="metric-row"><span class="metric-label">主力洗盤線(35):</span><span class="metric-value">{int(latest['主力洗盤線']):,}</span></div>
                    <div class="metric-row"><span class="metric-label">資金異動線(120):</span><span class="metric-value">{int(latest['資金異動線']):,}</span></div>
                    <div class="metric-row"><span class="metric-label">當前量能觸發:</span><span class="metric-value" style="color:{vol_sig_color};">{vol_sig_text}</span></div>
                </div>
            """,
            unsafe_allow_html=True,
        )

        # K線圖表渲染
        if kline_type == "日K":
            render_echarts_html(df, height=950)
        else:
            if df_60.empty:
                st.error(f"{clean_code} 暫時無法取得 60分鐘K資料")
            else:
                html_60 = render_echarts_html_60(df_60, height=950)
                components.html(html_60, height=960)

    else:
        st.error("查無數據或數據不足（少於 20 天），請重新確認股票代號。")
