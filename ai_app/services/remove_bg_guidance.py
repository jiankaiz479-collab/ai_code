"""去背失敗時的使用者改善建議。"""

from typing import Any, Mapping


BACKGROUND_REMOVAL_IMPROVEMENT_TIPS = [
    "調整去背參數設定，不同圖片可能需要不同參數。",
    "改用高品質、清晰的原始照片，提高去背準確性。",
    "去背後用 Photoshop 或 GIMP 手動修邊，特別是邊緣區域。",
    "同張圖可嘗試多次去背，再合併較好的結果。",
    "若現有工具效果有限，可嘗試其他模型（如 DeepLab、U^2-Net）。",
    "若有資料與資源，可訓練符合你場景的自定義去背模型。",
    "參考相關社群與論壇，吸收其他使用者的最佳實務。",
]


def get_remove_bg_improvement_tips(code: str, diagnosis: Mapping[str, Any] | None) -> list[str]:
    """根據去背失敗類型回傳可執行的改善建議。"""
    failure_type = (diagnosis or {}).get("failure_type")
    if str(code) == "1423" or failure_type == "background_not_removed":
        return BACKGROUND_REMOVAL_IMPROVEMENT_TIPS
    return []
