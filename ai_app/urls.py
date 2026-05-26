from django.urls import path
from .views import (
    RemoveBgView,
    TryCombineView,
    ReconstructView,
    Reconstruct_3D,
    TryOn3DOutfitView,
    HistoryPageView,
    HistoryApiView,
    HistoryMediaProxyView,
    ClothesExtractView,
)

urlpatterns = [
    # API 路徑 (對應 Excel 定義)
    path('virtual_try_on/clothes/remove_bg', RemoveBgView.as_view(), name='remove_bg'),
    path('virtual_try_on/fitting/generate', TryCombineView.as_view(), name='try_combine'),
    path('virtual_try_on/fitting/tryon_3d_physics', Reconstruct_3D.as_view(), name='tryon_3d_physics'),
    path('virtual_try_on/fitting/modules', ReconstructView.as_view(), name='ReconstructView'),
    # 一條龍：2D 試穿 → 3D 重建
    path('virtual_try_on/fitting/tryon_3d_outfit', TryOn3DOutfitView.as_view(), name='tryon_3d_outfit'),

    # 前端頁面
    path('history/', HistoryPageView.as_view(), name='history_page'),
    path('api/history/', HistoryApiView.as_view(), name='api_history'),
    path('api/history/media/<str:bucket>/<path:key>/', HistoryMediaProxyView.as_view(), name='history_media_proxy'),
    path('virtual_try_on/clothes/extract', ClothesExtractView.as_view(), name='clothes_extract'),
]
