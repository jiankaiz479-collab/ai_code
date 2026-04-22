import torch

class PIFuHDAdapter:
    def __init__(self):
        # 自動偵測設備：有 CUDA 就用 CUDA，沒有就乖乖用 CPU
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        
        # 顯示當前執行的硬體狀態，方便 Debug
        if self.device.type == 'cuda':
            print(f"✅ 偵測到 GPU: {torch.cuda.get_device_name(0)}，啟動硬體加速。")
        else:
            print("⚠️ 未偵測到 GPU，切換至 CPU 模式（運算速度較慢）。")

    def run_inference(self, model):
        # 加載模型時，強制對齊到偵測到的設備
        model.to(self.device)