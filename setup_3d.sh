# 路徑定義
BASE_DIR=$(pwd)
ENGINE_DIR="$BASE_DIR/ai_app/services/3d_engines"
VENV_PATH="$ENGINE_DIR/venv_pifuhd"
PIFU_SRC="$ENGINE_DIR/pifuhd"

echo "======================================================="
echo "🌟 正在配置 3D 重建環境基礎設施..."
echo "======================================================="

# 1. 檢查並安裝系統工具
echo "📦 步驟 1: 檢查 Ubuntu 系統工具..."
if dpkg -s freeglut3-dev ffmpeg libgl1-mesa-glx >/dev/null 2>&1; then
    echo "✅ 系統工具已安裝，跳過。"
else
    echo "📥 正在安裝系統工具 (需要 sudo)..."
    sudo apt-get update && sudo apt-get install -y freeglut3-dev ffmpeg libgl1-mesa-glx
fi

# 2. 檢查並建立虛擬環境
if [ -d "$VENV_PATH" ]; then
    echo "✅ 步驟 2: 虛擬環境 venv_pifuhd 已存在，跳過。"
else
    echo "🐍 步驟 2: 建立虛擬環境 venv_pifuhd..."
    python3 -m venv "$VENV_PATH"
fi

# 3. 進入環境並檢查 Python 套件
source "$VENV_PATH/bin/activate"

echo "📥 步驟 3: 檢查 Python 核心套件..."
# 檢查 torch 是否已安裝，避免重複下載幾百 MB
if python3 -c "import torch" >/dev/null 2>&1; then
    echo "✅ PyTorch 已安裝，跳過。"
else
    pip install --upgrade pip setuptools wheel
    if command -v nvidia-smi &> /dev/null; then
        echo "🚀 偵測到 GPU，安裝標準版 PyTorch..."
        pip install torch torchvision
    else
        echo "🐌 未偵測到 GPU，安裝 CPU 版 PyTorch..."
        pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    fi
fi

# 4. 檢查其餘依賴項
echo "📜 步驟 4: 檢查其餘依賴項..."
# 這裡檢查一個指標性的套件 (例如 trimesh)，如果沒有才跑安裝
if python3 -c "import trimesh, matplotlib" >/dev/null 2>&1; then
    echo "✅ 所有依賴套件已安裝，跳過。"
else
    if [ -f "$PIFU_SRC/requirements.txt" ]; then
        echo "📥 正在從 requirements.txt 安裝套件..."
        pip install -r "$PIFU_SRC/requirements.txt"
        pip install matplotlib
    else
        echo "⚠️ 進行手動安裝核心套件..."
        pip install Pillow scikit-image tqdm opencv-python trimesh PyOpenGL matplotlib
    fi
fi

echo "-------------------------------------------------------"
echo "🎉 3D 環境檢查與安裝完成！"
echo "👉 如果尚未修改，請手動進入程式碼修正 np.int 與 weights_only。"
echo "-------------------------------------------------------"