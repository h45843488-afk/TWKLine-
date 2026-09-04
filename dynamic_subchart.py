import pandas as pd
import numpy as np

def tdx_sma(series: pd.Series, n: int, m: int) -> pd.Series:
    """模擬通達信的 SMA(C, N, M) 權重移動平均"""
    alpha = m / n
    return series.ewm(alpha=alpha, adjust=False).mean()

def get_subchart_data(df: pd.DataFrame, metric_name: str = "主力資金"):
    if df.empty or len(df) < 75:
        return []

    data = df.copy()

    # 1. 計算主力資金
    llv_34 = data['Low'].rolling(window=34).min()
    hhv_34 = data['High'].rolling(window=34).max()
    rsv = 100 * (data['Close'] - llv_34) / (hhv_34 - llv_34 + 1e-9)
    data['主力資金'] = rsv.ewm(span=3, adjust=False).mean()

    fund = data['主力資金']
    close = data['Close']
    open_p = data['Open']
    low = data['Low']
    high = data['High']
    vol = data['Volume']
    n = len(data)

    # 2. 原汁原味主力進場 / 洗盤吸籌柱 (VAR1 ~ VAR5)
    data["ZL_VAR1"] = ((low + open_p + close + high) / 4).shift(1)
    zl_diff = low - data["ZL_VAR1"]

    zl_sma1 = tdx_sma(zl_diff.abs(), 13, 1)
    zl_positive = zl_diff.clip(lower=0)
    zl_sma2 = tdx_sma(zl_positive, 10, 1)

    data["ZL_VAR2"] = (zl_sma1 / zl_sma2.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0)
    data["ZL_VAR3"] = data["ZL_VAR2"].ewm(span=10, adjust=False).mean()
    data["ZL_VAR4"] = low.rolling(window=33, min_periods=1).min()

    zl_condition = np.where(low <= data["ZL_VAR4"], data["ZL_VAR3"], 0)
    data["ZL_VAR5"] = pd.Series(zl_condition, index=data.index, dtype="float64").ewm(span=3, adjust=False).mean()

    var5_scaled = data["ZL_VAR5"] * 25
    
    bar_magenta = []
    bar_green = []
    for i in range(n):
        val = var5_scaled.iloc[i]
        prev_val = var5_scaled.iloc[i-1] if i > 0 else 0
        if pd.notna(val) and val > 0:
            if i == 0 or val >= prev_val:
                bar_magenta.append(round(float(val), 2))
                bar_green.append(0)
            else:
                bar_magenta.append(0)
                bar_green.append(round(float(val), 2))
        else:
            bar_magenta.append(0)
            bar_green.append(0)

    # 3. 全新加入：大資金進場選股指標（大白柱邏輯）
    var1_h = low.shift(1)
    diff_h = (low - var1_h).abs()
    pos_diff_h = (low - var1_h).clip(lower=0)
    sma_abs_h = tdx_sma(diff_h, 13, 1)
    sma_max_h = tdx_sma(pos_diff_h, 13, 1)
    var2h = (sma_abs_h / sma_max_h.replace(0, np.nan)) * 4
    var3h = var2h.ewm(span=13, adjust=False).mean()
    var4h = low.rolling(window=34, min_periods=1).min()
    var5_cond_h = np.where(low <= var4h, var3h, 0)
    var5h = pd.Series(var5_cond_h, index=data.index).ewm(span=3, adjust=False).mean()

    llv_75 = low.rolling(window=75, min_periods=1).min()
    hhv_75 = high.rolling(window=75, min_periods=1).max()
    rsv_close = 100 * (close - llv_75) / (hhv_75 - llv_75 + 1e-9)
    rsv_open = 100 * (open_p - llv_75) / (hhv_75 - llv_75 + 1e-9)

    sma_rsv_c1 = tdx_sma(rsv_close, 20, 1)
    sma_rsv_c2 = tdx_sma(sma_rsv_c1, 15, 1)
    var6h = 100 - 3 * sma_rsv_c1 + 2 * sma_rsv_c2

    sma_rsv_o1 = tdx_sma(rsv_open, 20, 1)
    sma_rsv_o2 = tdx_sma(sma_rsv_o1, 15, 1)
    var7h = 100 - 3 * sma_rsv_o1 + 2 * sma_rsv_o2

    # VAR8H：結構突破 + 放量 + 收紅
    var8h = (var6h < var7h.shift(1)) & (vol > vol.shift(1)) & (close > close.shift(1))
    signal_count = var8h.astype(int).rolling(window=18, min_periods=1).sum()
    big_money_raw = var8h & (signal_count == 1)

    # 轉化為副圖大白柱高度 (觸發時給高柱 85，其餘為 0)
    bar_white = [85 if val else 0 for val in big_money_raw]

    # 4. ZIG 階梯多空趨勢線
    direction = [85] * n
    curr_dir = 85
    last_pivot = close.iloc[0]
    zig_pct = 0.05

    for i in range(1, n):
        c = close.iloc[i]
        if curr_dir == 85:
            if c < last_pivot: last_pivot = c
            elif c >= last_pivot * (1 + zig_pct): curr_dir = 15; last_pivot = c
        else:
            if c > last_pivot: last_pivot = c
            elif c <= last_pivot * (1 - zig_pct): curr_dir = 85; last_pivot = c
        direction[i] = curr_dir

    main_fund = [round(float(x), 2) if pd.notna(x) else 0 for x in fund]
    dir_line = [float(x) for x in direction]

    # 5. 參考線
    line_20, line_50, line_65, line_80 = [20]*n, [50]*n, [65]*n, [80]*n

    # 6. 訊號與防抖
    sig_chao, sig_jia_50, sig_jia_65, sig_bao, sig_kong = [None]*n, [None]*n, [None]*n, [None]*n, [None]*n
    cooldown, last_chao, last_jia, last_bao, last_kong = 8, -99, -99, -99, -99

    for i in range(1, n):
        f_curr, f_prev = fund.iloc[i], fund.iloc[i-1]
        if pd.isna(f_curr) or pd.isna(f_prev): continue

        if f_prev < 20 and f_curr >= 20 and (i - last_chao > cooldown):
            sig_chao[i] = main_fund[i]; last_chao = i
        if f_prev < 50 and f_curr >= 50 and (i - last_jia > cooldown):
            sig_jia_50[i] = main_fund[i]; last_jia = i
        if f_prev < 65 and f_curr >= 65 and (i - last_jia > cooldown):
            sig_jia_65[i] = main_fund[i]; last_jia = i
        if f_prev < 80 and f_curr >= 80 and (i - last_bao > cooldown):
            sig_bao[i] = main_fund[i]; last_bao = i
        if f_prev > 80 and f_curr <= 80 and (i - last_kong > cooldown):
            sig_kong[i] = main_fund[i]; last_kong = i

    # 7. ECharts Series 集合
    series_list = [
        # 大資金進場（純白大柱子 - 放量突破關鍵日）
        {
            "name": "大資金進場", "type": "bar", "data": bar_white,
            "xAxisIndex": 1, "yAxisIndex": 1,
            "itemStyle": {"color": "#FFFFFF", "opacity": 0.85}
        },
        # 主力進場（玫紅空心柱）
        {
            "name": "主力進場", "type": "bar", "data": bar_magenta,
            "xAxisIndex": 1, "yAxisIndex": 1,
            "itemStyle": {"color": "rgba(0,0,0,0)", "borderColor": "#FF00FF", "borderWidth": 1.5}
        },
        # 洗盤（綠色空心柱）
        {
            "name": "洗盤", "type": "bar", "data": bar_green,
            "xAxisIndex": 1, "yAxisIndex": 1,
            "itemStyle": {"color": "rgba(0,0,0,0)", "borderColor": "#00FF00", "borderWidth": 1.5}
        },
        # 主力資金紅線
        {
            "name": "主力資金", "type": "line", "data": main_fund,
            "xAxisIndex": 1, "yAxisIndex": 1, "smooth": True, "showSymbol": False,
            "lineStyle": {"width": 2, "color": "#FF0000"}
        },
        # 多空方向階梯黃線
        {
            "name": "多空方向", "type": "line", "data": dir_line,
            "xAxisIndex": 1, "yAxisIndex": 1, "step": "start", "showSymbol": False,
            "lineStyle": {"width": 2, "color": "#FFFF00"}
        },
        # 參考線
        {"name": "20抄底線", "type": "line", "data": line_20, "xAxisIndex": 1, "yAxisIndex": 1, "showSymbol": False, "lineStyle": {"width": 1, "color": "#FFFFFF"}},
        {"name": "50警戒線", "type": "line", "data": line_50, "xAxisIndex": 1, "yAxisIndex": 1, "showSymbol": False, "lineStyle": {"width": 1, "type": "dashed", "color": "#FFFF00"}},
        {"name": "65即將爆發", "type": "line", "data": line_65, "xAxisIndex": 1, "yAxisIndex": 1, "showSymbol": False, "lineStyle": {"width": 1, "type": "dot", "color": "#C0C0C0"}},
        {"name": "80爆發線", "type": "line", "data": line_80, "xAxisIndex": 1, "yAxisIndex": 1, "showSymbol": False, "lineStyle": {"width": 1, "color": "#FFFFFF"}},

        # 標籤區
        {
            "name": "★抄", "type": "scatter", "data": sig_chao, "xAxisIndex": 1, "yAxisIndex": 1,
            "symbol": "circle", "symbolSize": 18, "itemStyle": {"color": "#FF0000"},
            "label": {"show": True, "formatter": "抄", "color": "#FFFFFF", "fontSize": 11, "fontWeight": "bold"}
        },
        {
            "name": "★加50", "type": "scatter", "data": sig_jia_50, "xAxisIndex": 1, "yAxisIndex": 1,
            "symbol": "circle", "symbolSize": 16, "itemStyle": {"color": "#FFFF00"},
            "label": {"show": True, "formatter": "加", "color": "#000000", "fontSize": 11, "fontWeight": "bold"}
        },
        {
            "name": "★加65", "type": "scatter", "data": sig_jia_65, "xAxisIndex": 1, "yAxisIndex": 1,
            "symbol": "circle", "symbolSize": 16, "itemStyle": {"color": "#FFFF00"},
            "label": {"show": True, "formatter": "加", "color": "#000000", "fontSize": 11, "fontWeight": "bold"}
        },
        {
            "name": "★爆", "type": "scatter", "data": sig_bao, "xAxisIndex": 1, "yAxisIndex": 1,
            "symbol": "circle", "symbolSize": 18, "itemStyle": {"color": "#FFA500"},
            "label": {"show": True, "formatter": "爆", "color": "#000000", "fontSize": 12, "fontWeight": "bold"}
        },
        {
            "name": "★空", "type": "scatter", "data": sig_kong, "xAxisIndex": 1, "yAxisIndex": 1,
            "symbol": "circle", "symbolSize": 18, "itemStyle": {"color": "#00FF00"},
            "label": {"show": True, "formatter": "空", "color": "#000000", "fontSize": 11, "fontWeight": "bold"}
        }
    ]

    return series_list


def get_subchart_echarts_config(df: pd.DataFrame, metric_name: str = "主力資金"):
    return {
        "yAxis": {
            "gridIndex": 1,
            "min": 0,
            "max": 100,
            "splitLine": {"show": True, "lineStyle": {"type": "dashed", "color": "#333333"}}
        }
    }