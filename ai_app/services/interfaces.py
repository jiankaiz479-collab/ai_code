from abc import ABC, abstractmethod

# 這是我們定義的「規格書」
# ABC (Abstract Base Class) 代表這是一個抽象類別，不能直接執行，只能被繼承
class ImageProcessingInterface(ABC):

    @abstractmethod
    def remove_background(self, clothes_image) -> str:
        """
        功能：去背
        輸入：衣服圖片檔案 (clothes_image)
        輸出：處理後的圖片路徑 (str)
        """
        pass

    @abstractmethod
    def virtual_try_on(self, model_image, clothes_image) -> str:
        """
        功能：虛擬試穿
        輸入：模特兒圖片 (model_image), 衣服圖片 (clothes_image)
        輸出：合成後的圖片路徑 (str)
        """
        pass