"""mobile_adapter.py - 手機/桌機雙模自適應 K 線與 UI 模組

支援功能：
1. 一鍵切換手機端 (3x2 雙排黃底卡片) 與 桌機端 (單排寬版橫向) Header。
2. 通用頂部股票搜尋列組件 (可拉出首頁通用)。
3. 自動注入手機端緊湊 CSS 樣式與縮放優化。
4. ECharts 觸控浮窗自動關閉功能。
"""

import textwrap
import streamlit as st


def setup_mobile_page_config(page_title="台股 K 線監控站"):
    """設定 Streamlit 頁面配置，預設展開側邊欄 (expanded)"""
    st.set_page_config(
        page_title=page_title, layout="wide", initial_sidebar_state="expanded"
    )


def inject_mobile_css():
    """注入手機專用緊湊 CSS 樣式"""
    st.markdown(
        """
        <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        
        .block-container {
            padding-top: 0.4rem !important;
            padding-bottom: 0.5rem !important;
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
            max-width: 100% !important;
        }
        
        .mobile-top-tip {
            background-color: #f0f2f6;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 12px;
            color: #333333;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-left: 3px solid #ff4b4b;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_mobile_top_tip(
    tip_text="👈 <b>點擊左上角箭頭可展開左側資訊</b>", tag_text="📱 戰情室模式"
):
    """渲染手機版頂部操作提示列"""
    st.markdown(
        f"""
        <div class="mobile-top-tip">
            <span>{tip_text}</span>
            <span>{tag_text}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_search_bar(default_code="2330", key_prefix="main"):
    """通用頂部股票搜尋列組件
    :param default_code: 預設搜尋代號
    :param key_prefix: 元件 key 前綴（防止多頁面元件衝突）
    :return: (stock_id, is_submitted) 回傳輸入的股號與是否按下查詢按鈕
    """
    col_input, col_btn = st.columns([3, 1])

    with col_input:
        stock_id = st.text_input(
            "股票代號/名稱",
            value=default_code,
            placeholder="例如: 2330 或 台積電",
            label_visibility="collapsed",
            key=f"{key_prefix}_stock_input",
        )

    with col_btn:
        submitted = st.button(
            "🔍 查詢",
            use_container_width=True,
            key=f"{key_prefix}_search_btn",
        )

    return stock_id, submitted


def get_mobile_echarts_tooltip_config(show_tooltip=False):
    """傳回 ECharts 浮窗 (tooltip) 字典設定，預設關閉以防手機觸控遮擋"""
    return {"show": show_tooltip}


def apply_mobile_option(option: dict) -> dict:
    """自動將 ECharts option 加上手機端優化（關閉浮窗）"""
    option["tooltip"] = get_mobile_echarts_tooltip_config(show_tooltip=False)
    return option


def get_stock_header_html(
    stock_code="2330",
    stock_name="台積電",
    close_price=2425.00,
    change=15.00,
    pct_change=0.62,
    metrics_dict=None,
    is_mobile=True,
) -> str:
    """通用自適應 Header HTML 產生器
    :param is_mobile: True 產生手機雙排卡片；False 產生桌機單排寬版
    """
    if metrics_dict is None:
        metrics_dict = {
            "EMA5": "2409.6",
            "MA10": "2394.0",
            "MA20": "2389.5",
            "MA60": "2383.8",
            "成交量": "6,595張",
            "控盤值": "1.3",
        }

    sign = "+" if change > 0 else ""
    color = "#FF3333" if change >= 0 else "#00AA00"

    # 📱 手機版：雙排 3x2 黃底金邊卡片
    if is_mobile:
        grid_items_html = "".join([
            f'<div style="background: #fffbe6; border: 1px solid #ffe58f; padding: 6px 4px; border-radius: 6px;">'
            f'<div style="font-size: 11px; color: #888888; margin-bottom: 2px;">{k}</div>'
            f'<div style="font-size: 14px; font-weight: bold; color: #111111;">{v}</div>'
            f"</div>"
            for k, v in metrics_dict.items()
        ])

        raw_html = f"""
<div style="background-color: #ffffff; padding: 12px; border-radius: 8px; margin-bottom: 12px; border: 1px solid #f0f0f0; font-family: -apple-system, sans-serif;">
<div style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 8px; margin-bottom: 8px;">
<div style="font-size: 16px; font-weight: bold; color: #111111;">{stock_code} {stock_name}</div>
<div style="font-size: 16px; font-weight: bold; color: {color};">
${close_price:.2f} <span style="font-size: 12px; font-weight: normal;">{sign}{change:.2f} ({sign}{pct_change:.2f}%)</span>
</div>
</div>
<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; text-align: center;">
{grid_items_html}
</div>
</div>
"""

    # 💻 桌機版：單排橫向寬版
    else:
        row_items_html = "".join([
            f'<div style="margin-left: 15px; text-align: center;">'
            f'<span style="font-size: 11px; color: #888888; display: block;">{k}</span>'
            f'<span style="font-size: 14px; font-weight: bold; color: #111111;">{v}</span>'
            f"</div>"
            for k, v in metrics_dict.items()
        ])

        raw_html = f"""
<div style="display: flex; align-items: center; justify-content: space-between; background-color: #ffffff; padding: 10px 15px; border-radius: 8px; margin-bottom: 12px; border: 1px solid #f0f0f0; font-family: -apple-system, sans-serif;">
<div style="display: flex; align-items: center;">
<span style="font-size: 16px; font-weight: bold; color: #111111; margin-right: 12px;">{stock_code} {stock_name}</span>
<span style="font-size: 16px; font-weight: bold; color: {color};">
${close_price:.2f} <span style="font-size: 12px; font-weight: normal;">{sign}{change:.2f} ({sign}{pct_change:.2f}%)</span>
</span>
</div>
<div style="display: flex; align-items: center;">
{row_items_html}
</div>
</div>
"""

    return textwrap.dedent(raw_html).strip()


def init_mobile_adapter(page_title="台股 K 線監控站", show_tip=True):
    """一鍵初始化設定"""
    setup_mobile_page_config(page_title=page_title)
    inject_mobile_css()
    if show_tip:
        render_mobile_top_tip()