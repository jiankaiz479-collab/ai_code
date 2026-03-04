from django.urls import path
from .views import RemoveBgView, TryCombineView

urlpatterns = [
    # API 路徑 (對應 Excel 定義)
    path('virtual_try_on/clothes/remove_bg', RemoveBgView.as_view(), name='remove_bg'),
    path('virtual_try_on/fitting/generate', TryCombineView.as_view(), name='try_combine'),
    
]