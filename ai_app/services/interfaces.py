"""服務層抽象介面集中地。

這個檔案放所有「合約」——具體實作（processing.py / preprocessors/）只認這些介面。
新人查抽象只看這一個檔案。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image


# ============================================================
# 處理器整體合約（AIProcessor 必須實作的方法群）
#
# 註：去背已改由下方 `RemoveBgPipeline` 策略合約統一管理，
#     此處不再規範 `remove_background`。AIProcessor 仍保留該方法作為
#     legacy pipeline 的內部實作，但不屬於公開合約。
# ============================================================
class ImageProcessingInterface(ABC):

    @abstractmethod
    def virtual_try_on(self, model_image, clothes_image) -> str:
        """
        功能：虛擬試穿
        輸入：模特兒圖片 (model_image), 衣服圖片 (clothes_image)
        輸出：合成後的圖片路徑 (str)
        """
        ...


# ============================================================
# 去背流程策略合約（Strategy Pattern）
# ============================================================
@dataclass
class RemoveBgResult:
    """去背流程的標準回傳格式。

    Fields:
        image: 處理後的 PIL Image（成功時），None（失敗時）
        ok: 是否成功
        code: 業務碼字串（"1200" 成功、"1xxx" 失敗）
        error_detail: 失敗時的詳細訊息（給 debug_info.error_detail）
        diagnosis: 結構化失敗診斷（給 debug_info.diagnosis），可選
    """
    image: Optional[Image.Image]
    ok: bool
    code: str
    error_detail: Optional[str] = None
    diagnosis: dict = field(default_factory=dict)


class RemoveBgPipeline(ABC):
    """去背流程抽象基底類別。

    不同實作（Legacy / Robust v1 / Robust v2 / Commercial …）都繼承此類別。
    工廠 `preprocessors.factory.get_remove_bg_pipeline()` 依 env 決定回哪個實作。
    """

    @abstractmethod
    def process(self, pil_image: Image.Image) -> RemoveBgResult:
        """執行去背流程。

        Args:
            pil_image: 輸入圖（已開啟成 PIL Image）

        Returns:
            RemoveBgResult: 統一格式的結果
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """實作名稱，用於 log 標記（例如 "legacy" / "robust_v1"）。"""
        ...
