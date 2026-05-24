import os
import sys
import types
import unittest
from unittest.mock import patch

from ai_app.services.preprocessors import get_remove_bg_pipeline
from ai_app.services.preprocessors.legacy import LegacyRemoveBg


class _DummyPipeline:
    def __init__(self, processor):
        self.processor = processor


class GetRemoveBgPipelineTests(unittest.TestCase):
    def test_defaults_to_legacy(self):
        with patch.dict(os.environ, {}, clear=True):
            pipeline = get_remove_bg_pipeline(processor=object())
        self.assertIsInstance(pipeline, LegacyRemoveBg)

    def test_selects_robust_v2_with_normalized_env_value(self):
        fake_module = types.ModuleType("ai_app.services.preprocessors.robust_v2")
        fake_module.RobustV2RemoveBg = _DummyPipeline

        with patch.dict(sys.modules, {"ai_app.services.preprocessors.robust_v2": fake_module}):
            with patch.dict(os.environ, {"REMOVE_BG_VERSION": " Robust_V2 "}, clear=True):
                pipeline = get_remove_bg_pipeline(processor="processor")

        self.assertIsInstance(pipeline, _DummyPipeline)
        self.assertEqual(pipeline.processor, "processor")

    def test_selects_robust_v3(self):
        fake_module = types.ModuleType("ai_app.services.preprocessors.v3_router")
        fake_module.V3RouterRemoveBg = _DummyPipeline

        with patch.dict(sys.modules, {"ai_app.services.preprocessors.v3_router": fake_module}):
            with patch.dict(os.environ, {"REMOVE_BG_VERSION": "robust_v3"}, clear=True):
                pipeline = get_remove_bg_pipeline(processor="processor")

        self.assertIsInstance(pipeline, _DummyPipeline)
        self.assertEqual(pipeline.processor, "processor")


if __name__ == "__main__":
    unittest.main()
