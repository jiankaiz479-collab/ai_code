#!/bin/bash

# API 测试脚本 - 测试内部 127.0.0.1:8002

BASE_URL="http://127.0.0.1:8002"
TEST_IMAGE="media/modules_0cbd57a5.png"
RESULT_DIR="/tmp/api_test_results"

# 创建结果目录
mkdir -p "$RESULT_DIR"

echo "======================================================="
echo "开始 API 内部测试"
echo "Base URL: $BASE_URL"
echo "======================================================="
echo ""

# 检查测试图片是否存在
if [ ! -f "$TEST_IMAGE" ]; then
    echo "❌ 错误：找不到测试图片 $TEST_IMAGE"
    exit 1
fi

# 1. 去背 API 测试
echo "[1/4] 测试 remove_bg 端点..."
echo "POST /virtual_try_on/clothes/remove_bg"
RESPONSE_1=$(curl -s -w "\n%{http_code}" -X POST \
    -F "clothes_image=@$TEST_IMAGE" \
    "$BASE_URL/virtual_try_on/clothes/remove_bg" \
    -o "$RESULT_DIR/remove_bg_response.multipart")
HTTP_CODE_1=$(tail -n 1 <<< "$RESPONSE_1")
echo "HTTP Status: $HTTP_CODE_1"
if [ "$HTTP_CODE_1" = "200" ]; then
    echo "✅ 成功"
    # 提取 JSON 部分
    xxd -l 600 -g 1 "$RESULT_DIR/remove_bg_response.multipart" | grep -o '"code": [0-9]*' | head -1
else
    echo "❌ 失败"
fi
echo ""

# 2. 虚拟试穿 API 测试（需要 model_image 和 garment_images + JSON data）
echo "[2/4] 测试 generate (虚拟试穿) 端点..."
echo "POST /virtual_try_on/fitting/generate"
# 创建 JSON 格式的参数
GARMENT_DATA='{"garments": [{"position": "upper", "color": "default"}]}'
HTTP_CODE_2=$(curl -s -w "%{http_code}" -X POST \
    -F "model_image=@$TEST_IMAGE" \
    -F "garment_images=@$TEST_IMAGE" \
    -F "data=$GARMENT_DATA" \
    "$BASE_URL/virtual_try_on/fitting/generate" \
    -o "$RESULT_DIR/generate_response.json")
echo "HTTP Status: $HTTP_CODE_2"
if [ "$HTTP_CODE_2" = "200" ] || [ "$HTTP_CODE_2" = "201" ]; then
    echo "✅ 成功"
    head -c 300 "$RESULT_DIR/generate_response.json"
    echo ""
else
    echo "❌ 失败（需要完整的虚拟试穿数据）"
    head -c 300 "$RESULT_DIR/generate_response.json"
    echo ""
fi
echo ""

# # 3. 3D 物理引擎端点测试
# echo "[3/4] 测试 tryon_3d_physics 端点..."
# echo "POST /virtual_try_on/fitting/tryon_3d_physics"
# HTTP_CODE_3=$(curl -s -w "%{http_code}" -X POST \
#     -F "model_image=@$TEST_IMAGE" \
#     -F "clothes_image=@$TEST_IMAGE" \
#     "$BASE_URL/virtual_try_on/fitting/tryon_3d_physics" \
#     -o "$RESULT_DIR/tryon_3d_physics_response.json")
# echo "HTTP Status: $HTTP_CODE_3"
# if [ "$HTTP_CODE_3" = "200" ] || [ "$HTTP_CODE_3" = "201" ]; then
#     echo "✅ 成功"
#     head -c 200 "$RESULT_DIR/tryon_3d_physics_response.json"
#     echo ""
# else
#     echo "❌ 失败（通常需要特定输入参数）"
# fi
# echo ""

# 3. 3D 物理引擎端点测试
echo "[3/4] 测试 tryon_3d_physics 端点..."
echo "POST /virtual_try_on/fitting/tryon_3d_physics"
HTTP_CODE_3=$(curl -s -w "%{http_code}" -X POST \
    -F "model_image=@$TEST_IMAGE" \
    -F "clothes_image=@$TEST_IMAGE" \
    "$BASE_URL/virtual_try_on/fitting/tryon_3d_physics" \
    -o "$RESULT_DIR/tryon_3d_physics_response.json")
echo "HTTP Status: $HTTP_CODE_3"
if [ "$HTTP_CODE_3" = "200" ] || [ "$HTTP_CODE_3" = "201" ]; then
    echo "✅ 成功"
    head -c 300 "$RESULT_DIR/tryon_3d_physics_response.json"
    echo ""
else
    echo "⚠️  返回非200（可能需要特定配置）"
    head -c 300 "$RESULT_DIR/tryon_3d_physics_response.json"
    echo ""
fi
echo ""

# 4. 模块识别端点测试
echo "[4/4] 测试 modules 端点..."
echo "POST /virtual_try_on/fitting/modules"
HTTP_CODE_4=$(curl -s -w "%{http_code}" -X POST \
    -F "model_image=@$TEST_IMAGE" \
    "$BASE_URL/virtual_try_on/fitting/modules" \
    -o "$RESULT_DIR/modules_response.json")
echo "HTTP Status: $HTTP_CODE_4"
if [ "$HTTP_CODE_4" = "200" ] || [ "$HTTP_CODE_4" = "201" ]; then
    echo "✅ 成功"
    head -c 300 "$RESULT_DIR/modules_response.json"
    echo ""
else
    echo "⚠️  返回非200（可能需要特定配置）"
    head -c 300 "$RESULT_DIR/modules_response.json"
    echo ""
fi
echo ""

echo "======================================================="
echo "测试完成"
echo "结果文件存放在: $RESULT_DIR"
echo "======================================================="
