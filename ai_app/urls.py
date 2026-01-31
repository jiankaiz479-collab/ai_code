from django.urls import path
from .views import RemoveBgView, TryCombineView, DebugPageView

urlpatterns = [
    # API 路徑 (對應 Excel 定義)
    path('api/remove_bg', RemoveBgView.as_view(), name='remove_bg'),
    path('api/try_combine', TryCombineView.as_view(), name='try_combine'),
    
]