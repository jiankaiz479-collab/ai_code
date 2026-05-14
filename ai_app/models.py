from django.db import models

class HistoryRecord(models.Model):
    """
    紀錄每一次 AI 任務的執行結果與 MinIO 圖片路徑
    """
    operation = models.CharField(max_length=50, help_text="操作類型: remove_bg, try_on_2d, try_on_3d")
    status = models.CharField(max_length=20, default="success", help_text="狀態: success, failed")
    bucket = models.CharField(max_length=50, default="history-images")
    object_key = models.CharField(max_length=255, help_text="原圖在 MinIO 的路徑", null=True, blank=True)
    thumb_key = models.CharField(max_length=255, help_text="縮圖在 MinIO 的路徑", null=True, blank=True)
    response_json = models.JSONField(help_text="完整的執行結果 JSON", null=True, blank=True)
    start_ts = models.DateTimeField(help_text="開始時間")
    end_ts = models.DateTimeField(help_text="結束時間")
    exec_time_ms = models.IntegerField(help_text="總耗時 (毫秒)", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']  # 最新的排在最前面

    def __str__(self):
        return f"[{self.operation}] {self.status} at {self.created_at}"
