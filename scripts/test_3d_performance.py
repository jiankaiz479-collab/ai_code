"""
3D API 效能測試腳本
用來對比「關閉 Refine」與「開啟 Refine」的時間差異

用法:
    python scripts/test_3d_performance.py
"""

import time
import requests
import sys
from pathlib import Path
import io
from PIL import Image

# 設定您的 API URL
API_URL = "http://localhost:8002/virtual_try_on/fitting/tryon_3d_physics"

def test_3d_endpoint(image_path: str, refine: bool, resolution: int = 1024):
    print(f"\n🚀 開始測試 3D 重建 API (解析度={resolution}x{resolution}, Refine={refine})...")
    
    try:
        # 使用 Pillow 讀取並強制縮放圖片
        img = Image.open(image_path)
        img = img.resize((resolution, resolution), Image.LANCZOS)
        
        # 將縮放後的圖片存入記憶體，避免產生多餘實體檔案
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)
        
        # 設定上傳的檔名與 MIME type
        files = {'model_image': ('test_image.png', img_bytes, 'image/png')}
        data = {'refine': 'true' if refine else 'false'}
        
        start_time = time.time()
        
        # 設定 300 秒 timeout，因為 3D API 會用 Long polling 卡住直到成功
        response = requests.post(API_URL, files=files, data=data, timeout=300)
        end_time = time.time()
        
        elapsed = end_time - start_time
            
        if response.status_code == 200:
            print(f"✅ 測試成功！總耗時: {elapsed:.2f} 秒")
            
            # API 回傳的是 multipart/mixed，我們需要解析出其中的 GLB 區段
            content_type = response.headers.get('Content-Type', '')
            if 'boundary=' in content_type:
                boundary = content_type.split('boundary=')[1].encode('utf-8')
                parts = response.content.split(b'--' + boundary)
                
                glb_saved = False
                for part in parts:
                    # 尋找包含 3D 模型資料的段落
                    if b'Content-Type: model/gltf-binary' in part:
                        # 標頭與實際內容之間會由連續的兩個換行 (\r\n\r\n) 分隔
                        header_and_body = part.split(b'\r\n\r\n', 1)
                        if len(header_and_body) == 2:
                            glb_bytes = header_and_body[1]
                            # 移除該區段結尾多餘的換行符號
                            if glb_bytes.endswith(b'\r\n'):
                                glb_bytes = glb_bytes[:-2]
                                
                            out_name = f"test_result_{resolution}x{resolution}.glb"
                            with open(out_name, "wb") as f:
                                f.write(glb_bytes)
                            print(f"📁 成功提取 3D 模型，已存至: {out_name}")
                            glb_saved = True
                            break
                if not glb_saved:
                    print("⚠️ 解析失敗：在回傳內容中找不到 GLB 檔案！")
            else:
                print("⚠️ API 回傳格式錯誤 (非 multipart)，無法提取 GLB。")
        else:
            print(f"❌ 測試失敗！HTTP 狀態碼: {response.status_code}")
            print(f"錯誤訊息: {response.text}")
            
    except FileNotFoundError:
        print(f"⚠️ 找不到圖片檔案：{image_path}，請確認您輸入的路徑是否正確。")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"⚠️ API 請求發生錯誤: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("⚠️ 錯誤：請提供測試圖片的路徑！")
        print("💡 用法範例: python scripts/test_3d_performance.py <你的圖片路徑.jpg>")
        sys.exit(1)

    target_image = sys.argv[1]

    print("==================================================")
    print("  自動化效能測試：Tripo 3D 解析度 (1024 vs 512) 對比")
    print("==================================================")
    
    # # 1. 測試 1024x1024 (對照組)
    test_3d_endpoint(image_path=target_image, refine=False, resolution=1024)
    
    print("\n⏳ 休息 10 秒鐘，避免連續請求觸發 Tripo 速率限制 (4429)...\n")
    time.sleep(10)
    
    # 2. 測試 512x512 (實驗組)
    test_3d_endpoint(image_path=target_image, refine=False, resolution=512)
    
    print("\n==================================================")
    print(" 🎉 測試結束！趕快把這兩次的耗時數據記錄下來吧！")
    print("==================================================")