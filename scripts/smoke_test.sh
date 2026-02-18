#!/usr/bin/env bash
# 端到端 Smoke Test 脚本
# 验证文档生成系统的关键功能

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_ROOT/build"

echo "=========================================="
echo "文档生成系统 Smoke Test"
echo "=========================================="

# 清理旧产物
rm -f "$BUILD_DIR/smoke_test_output.json" "$BUILD_DIR/smoke_test_output.docx"

# 测试 1: 单元测试
echo ""
echo "[1/4] 运行单元测试..."
cd "$PROJECT_ROOT"
if python3 -m pytest tests/ -v --tb=short; then
    echo "✅ 单元测试通过"
else
    echo "❌ 单元测试失败"
    exit 1
fi

# 测试 2: CLI JSON 输出
echo ""
echo "[2/4] 测试 CLI JSON 输出..."
if python3 -m app.cli score --input sample_shigong.txt --out "$BUILD_DIR/smoke_test_output.json" > /dev/null 2>&1; then
    if [ -f "$BUILD_DIR/smoke_test_output.json" ]; then
        SIZE=$(wc -c < "$BUILD_DIR/smoke_test_output.json")
        echo "✅ JSON 输出成功 ($SIZE bytes)"
    else
        echo "❌ JSON 文件未生成"
        exit 1
    fi
else
    echo "❌ CLI JSON 输出失败"
    exit 1
fi

# 测试 3: CLI DOCX 输出
echo ""
echo "[3/4] 测试 CLI DOCX 输出..."
if python3 -m app.cli score --input sample_shigong.txt --docx-out "$BUILD_DIR/smoke_test_output.docx" > /dev/null 2>&1; then
    if [ -f "$BUILD_DIR/smoke_test_output.docx" ]; then
        SIZE=$(wc -c < "$BUILD_DIR/smoke_test_output.docx")
        echo "✅ DOCX 输出成功 ($SIZE bytes)"
    else
        echo "❌ DOCX 文件未生成"
        exit 1
    fi
else
    echo "❌ CLI DOCX 输出失败"
    exit 1
fi

# 测试 4: JSON 结构验证
echo ""
echo "[4/4] 验证 JSON 结构..."
if python3 -c "
import json
import sys
with open('$BUILD_DIR/smoke_test_output.json') as f:
    data = json.load(f)
required_keys = ['total_score', 'dimension_scores', 'penalties', 'suggestions', 'meta']
missing = [k for k in required_keys if k not in data]
if missing:
    print(f'缺少字段: {missing}')
    sys.exit(1)
if not isinstance(data['total_score'], (int, float)):
    print('total_score 类型错误')
    sys.exit(1)
print(f'total_score: {data[\"total_score\"]}')
print(f'维度数量: {len(data[\"dimension_scores\"])}')
print(f'扣分项数量: {len(data[\"penalties\"])}')
"; then
    echo "✅ JSON 结构验证通过"
else
    echo "❌ JSON 结构验证失败"
    exit 1
fi

echo ""
echo "=========================================="
echo "✅ 所有 Smoke Test 通过！"
echo "=========================================="
echo ""
echo "产物路径:"
echo "  - JSON: $BUILD_DIR/smoke_test_output.json"
echo "  - DOCX: $BUILD_DIR/smoke_test_output.docx"
