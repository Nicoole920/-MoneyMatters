import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pandas as pd
import twstock
import yfinance as yf

# --- 1. 動態生成股票與 ETF 中文名稱對照表 (整合 twstock) ---
stock_map = {
    code: info.name 
    for code, info in twstock.codes.items() 
    if info.type in ['股票', 'ETF'] and '債' not in info.name and '債' not in info.group
}

# --- 2. ETF 代碼清單 ---
etf_tickers = [
    "00403A.TW", "0050.TW", "0052.TW", "0056.TW", "006208.TW", 
    "00631L.TW", "00646.TW", "00662.TW", "00679B.TW", "00687B.TW", 
    "00692.TW", "00713.TW", "00720B.TW", "00724B.TW", "00725B.TW", 
    "00733.TW", "00751B.TW", "00757.TW", "00772B.TW", "00850.TW", 
    "00878.TW", "00881.TW", "00900.TW", "00905.TW", "00918.TW", 
    "00919.TW", "00929.TW", "00933B.TW", "00937B.TW", "00940.TW", 
    "009816.TW", "00981A.TW"
]

def get_etf_holdings(symbol):
    """抓取單一 ETF 的成分股明細與 AUM"""
    try:
        query_symbol = f"{symbol}.TW" if not symbol.endswith(('.TW', '.TWO')) else symbol
        ticker = yf.Ticker(query_symbol)
        
        # 抓取基金規模 (AUM) 數據
        aum = ticker.info.get('totalAssets', 0)
        holdings = ticker.funds_data.top_holdings
        
        if holdings is not None and not holdings.empty:
            df = holdings.copy()
            raw_holding_values = df.iloc[:, -1].values  
            
            clean_etf_code = symbol.split('.')[0]
            
            df['ETF代碼'] = clean_etf_code
            df['ETF名稱'] = stock_map.get(clean_etf_code, f"ETF_{clean_etf_code}")
            df['個股代碼'] = df.index.map(lambda x: str(x).replace('.TW', '').replace('.TWO', ''))
            df['個股名稱'] = df['個股代碼'].apply(lambda x: stock_map.get(x, ""))
            df['持股比例(%)'] = raw_holding_values
            
            # 將 AUM 轉換為億元並存入欄位（保留浮點數供排序）
            df['基金AUM(億)'] = round(aum / 100000000, 2) if aum else 0
            
            return df[['個股代碼', '個股名稱', 'ETF代碼', 'ETF名稱', '持股比例(%)', '基金AUM(億)']]
    except Exception as e:
        pass
    return None

def generate_stock_to_etf_report(etf_list):
    all_data = []
    for etf in etf_list:
        df_single = get_etf_holdings(etf)
        if df_single is not None:
            all_data.append(df_single)
            
    if not all_data:
        return "❌ 未能成功抓取任何 ETF 成分股資料。"
        
    combined_df = pd.concat(all_data, ignore_index=True)
    combined_df = combined_df.sort_values(by=['個股代碼', '基金AUM(億)'], ascending=[True, False])
    
    # 將原本 print 的內容改寫入 output_lines 陣列中
    output_lines = []
    output_lines.append("【所有成分股股票(名稱&代碼)清單】\n")
    
    unique_stocks = combined_df[['個股代碼', '個股名稱']].drop_duplicates().reset_index(drop=True)
    stock_list_strings = []
    for idx, row in unique_stocks.iterrows():
        stock_name = row['個股名稱']
        stock_display = f"{stock_name}({row['個股代碼']})" if stock_name else f"({row['個股代碼']})"
        stock_list_strings.append(stock_display)
        
    output_lines.append("LIST = [")
    for i in range(0, len(stock_list_strings), 5):
        chunk = stock_list_strings[i:i+5]
        formatted_chunk = ", ".join([f'"{item}"' for item in chunk])
        ends_comma = "," if i + 5 < len(stock_list_strings) else ""
        output_lines.append(f"    {formatted_chunk}{ends_comma}")
    output_lines.append("]\n")
    
    output_lines.append("【股票 ➡️ 持有該股票的基金(ETF)明細表 (區塊內依 AUM 排序)】\n")
    last_stock_code = None
    for _, row in combined_df.iterrows():
        current_stock_code = row['個股代碼']
        if last_stock_code is not None and current_stock_code != last_stock_code:
            output_lines.append("============================================================")
        
        rounded_percent = round(float(row['持股比例(%)']), 2)
        rounded_aum = round(float(row['基金AUM(億)']))
        stock_name = row['個股名稱']
        stock_display = f"{stock_name}({current_stock_code})" if stock_name else f"({current_stock_code})"
        aum_display = f" {rounded_aum}億AUM" if rounded_aum > 0 else ""
        
        output_lines.append(f"{stock_display} {row['ETF名稱']}({row['ETF代碼']}){aum_display} 持有{rounded_percent}%")
        last_stock_code = current_stock_code
        
    return "\n".join(output_lines)


def send_line_message(content):
    """透過 LINE Messaging API 發送推播訊息"""
    # 從 GitHub Secrets 讀取 LINE 憑證
    line_access_token = os.environ.get("LINE_ACCESS_TOKEN")
    line_user_id = os.environ.get("LINE_USER_ID")
    
    if not line_access_token or not line_user_id:
        print("❌ 錯誤：未設定 LINE_ACCESS_TOKEN 或 LINE_USER_ID 環境變數")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {line_access_token}"
    }
    
    # ⚠️ LINE 單則文字訊息上限為 5000 字
    # 如果您的 ETF 成分股總字數超過 5000 字，需要進行分段發送
    max_length = 4500
    message_chunks = [content[i:i+max_length] for i in range(0, len(content), max_length)]
    
    try:
        for chunk in message_chunks:
            payload = {
                "to": line_user_id,
                "messages": [
                    {
                        "type": "text",
                        "text": chunk
                    }
                ]
            }
            response = requests.post(url, headers=headers, json=payload)
            
            if response.status_code == 200:
                print("🟢 LINE 訊息發送成功！")
            else:
                print(f"❌ LINE 發送失敗，狀態碼：{response.status_code}，回應：{response.text}")
                
    except Exception as e:
        print(f"❌ 呼叫 LINE API 時發生異常: {e}")

if __name__ == "__main__":
    report_content = generate_stock_to_etf_report(etf_tickers)
    send_line_message(report_content)
