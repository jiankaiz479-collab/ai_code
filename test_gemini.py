import os
import sys
import time
from pathlib import Path


def _add_venv_site_packages() -> None:
    repo_root = Path(__file__).resolve().parent
    venv_site_packages = repo_root / "venv" / "lib"
    if not venv_site_packages.exists():
        return

    for candidate in venv_site_packages.glob("python*/site-packages"):
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
        return


_add_venv_site_packages()

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args, **kwargs):
        """Fallback: keep script runnable even when python-dotenv is unavailable."""
        return False

from google import genai

# 載入環境變數
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

print("--- Gemini 模型清單查詢工具 (修正版) ---")

if not api_key:
    print("❌ 錯誤: 找不到 GOOGLE_API_KEY，請檢查 .env 檔案")
    exit()

try:
    # 設定客戶端
    client = genai.Client(api_key=api_key)
    print(f"✅ Client 已建立，正在向 Google 請求模型清單...\n")

    # --- 動作：查詢並詳細列出所有模型 ---
    # 這裡調整表格寬度，讓顯示更清楚
    print(f"{'模型 ID (Model Name)':<40} | {'顯示名稱 (Display Name)'}")
    print("-" * 80)

    count = 0
    # client.models.list() 回傳的是新版的 Model 物件
    for m in client.models.list():
        count += 1
        
        # 1. 處理模型 ID (把 models/ 去掉)
        model_id = m.name.replace("models/", "")
        
        # 2. 安全取得顯示名稱 (如果沒有 display_name 屬性，就用 ID 代替)
        # 使用 getattr(物件, '屬性名', '預設值') 來防止報錯
        display_name = getattr(m, 'display_name', 'N/A')
        if not display_name: # 有時候屬性存在但內容是空的
            display_name = "N/A"

        # 3. 嘗試判斷模型類型 (因為新版 SDK 可能沒有 methods 屬性，我們改用名字判斷)
        # 這是一種簡單的判斷邏輯
        model_type = "通用/對話"
        if "embed" in model_id.lower():
            model_type = "嵌入 (Embedding)"
        elif "imagen" in model_id.lower():
            model_type = "繪圖 (Imagen)"
            
        # 格式化輸出
        print(f"{model_id:<40} | {display_name} ({model_type})")

    print("-" * 80)
    print(f"\n📊 總共找到 {count} 個模型可用。")

    # --- 簡單連線測試 ---
    target_test_model = "gemini-2.5-flash-image" 
    print(f"\n🚀 正在進行簡單連線測試 (使用 {target_test_model})...")
    time.sleep(2) 
    
    response = client.models.generate_content(
        model=target_test_model,
        contents="Hi, confirm you are online."
    )
    print(f"✅ 測試回應成功: {response.text.strip()}")

except Exception as e:
    print("\n❌ 發生錯誤：")
    print(e)
    
    # 如果還是有錯，可以把下面這行註解打開，看看 m 裡面到底有什麼
    # print(dir(e))