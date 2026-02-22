import base64
import json
import cv2
import numpy as np
from typing import List, Dict, Optional, Tuple
from collections import Counter

import requests


def screenshot_to_base64(screenshot_bytes: bytes) -> str:
    return base64.standard_b64encode(screenshot_bytes).decode("utf-8")


def bytes_to_cv_image(img_bytes: bytes) -> Optional[np.ndarray]:
    """将图片字节转换为 OpenCV 图像"""
    if img_bytes is None:
        return None
    nparr = np.frombuffer(img_bytes, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)


def cv_image_to_base64(img: np.ndarray) -> str:
    """将 OpenCV 图像转换为 base64"""
    _, buffer = cv2.imencode('.png', img)
    return base64.standard_b64encode(buffer).decode("utf-8")


# ==================== 图像处理方法 ====================

def process_original(img: np.ndarray) -> np.ndarray:
    """原图"""
    return img


def process_grayscale(img: np.ndarray) -> np.ndarray:
    """灰度图"""
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return img


def process_edges(img: np.ndarray) -> np.ndarray:
    """边缘检测"""
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    edges = cv2.Canny(gray, 50, 150)
    return cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)


def process_enhanced_contrast(img: np.ndarray) -> np.ndarray:
    """增强对比度"""
    if len(img.shape) == 3:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        lab = cv2.merge([l, a, b])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return img


def process_highlight_dark(img: np.ndarray) -> np.ndarray:
    """高亮暗色区域（缺口通常较暗）"""
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    # 反转：暗区变亮
    inverted = 255 - gray

    # 增强
    enhanced = cv2.normalize(inverted, None, 0, 255, cv2.NORM_MINMAX)

    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def process_sobel(img: np.ndarray) -> np.ndarray:
    """Sobel 梯度"""
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    sobel = np.sqrt(sobel_x**2 + sobel_y**2)
    sobel = np.uint8(np.clip(sobel, 0, 255))

    return cv2.cvtColor(sobel, cv2.COLOR_GRAY2BGR)


def process_with_markers(img: np.ndarray, puzzle_width: int = 60, step: int = 20) -> np.ndarray:
    """在图片上标注候选位置，让 AI 选择"""
    h, w = img.shape[:2]
    result = img.copy()

    # 在可能的缺口位置画标记
    markers = []
    for i, x in enumerate(range(80, w - puzzle_width, step)):
        marker_id = chr(ord('A') + i) if i < 26 else str(i)
        markers.append((marker_id, x))

        # 画垂直线
        color = (0, 0, 255)  # 红色
        cv2.line(result, (x, 0), (x, h), color, 1)

        # 标注字母/数字
        cv2.putText(result, marker_id, (x - 5, 15),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    # 标注拼图块
    cv2.rectangle(result, (0, 0), (puzzle_width, h), (0, 255, 0), 2)

    return result, markers


def process_with_ruler(img: np.ndarray, puzzle_width: int = 60) -> np.ndarray:
    """添加刻度尺标注，帮助 AI 精确定位"""
    h, w = img.shape[:2]

    # 创建更大的画布，顶部留出空间给刻度尺
    ruler_height = 30
    result = np.ones((h + ruler_height, w, 3), dtype=np.uint8) * 255

    # 将原图放到下方
    result[ruler_height:, :] = img

    # 画刻度线和数字（更大更清晰）
    for x in range(0, w + 1, 10):
        y_base = ruler_height
        if x % 50 == 0:
            # 主刻度线（每50px）- 红色粗线
            cv2.line(result, (x, 0), (x, y_base), (0, 0, 255), 2)
            # 数字标注
            label = str(x)
            cv2.putText(result, label, (x - len(label)*4, y_base - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        else:
            # 次刻度线（每10px）- 灰色细线
            cv2.line(result, (x, y_base - 10), (x, y_base), (150, 150, 150), 1)

    # 在图片上画垂直参考线（每50px）
    for x in range(50, w, 50):
        cv2.line(result, (x, ruler_height), (x, h + ruler_height), (0, 0, 255), 1)
        # 底部也标注数字
        cv2.putText(result, str(x), (x - 10, h + ruler_height - 5),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)

    # 标注拼图块区域（���色框）
    cv2.rectangle(result, (0, ruler_height), (puzzle_width, h + ruler_height), (0, 255, 0), 3)
    cv2.putText(result, "PIECE", (5, ruler_height + 20),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    return result


def process_with_grid(img: np.ndarray, puzzle_width: int = 60) -> np.ndarray:
    """添加网格线，帮助 AI 定位"""
    h, w = img.shape[:2]
    result = img.copy()

    # 垂直网格线（每20px）
    for x in range(0, w + 1, 20):
        color = (0, 200, 0) if x % 100 == 0 else (200, 200, 200)
        thickness = 2 if x % 100 == 0 else 1
        cv2.line(result, (x, 0), (x, h), color, thickness)

    # 在100px位置标注数字
    for x in range(0, w + 1, 100):
        cv2.putText(result, str(x), (x + 2, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

    # 标注拼图块区域
    cv2.rectangle(result, (0, 0), (puzzle_width, h), (0, 255, 0), 2)

    return result


# 所有处理方法
IMAGE_PROCESSORS = {
    "original": ("原图", process_original),
    "ruler": ("带刻度尺", process_with_ruler),
    "grid": ("带网格", process_with_grid),
    "edges": ("边缘检测", process_edges),
    "contrast": ("增强对比度", process_enhanced_contrast),
}


# ==================== AI 调用 ====================

def analyze_click_captcha(api_key, model, screenshot_base64, prompt_text):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                    {
                        "type": "text",
                        "text": f'''这是一个验证码图片，是一个 3x3 的图片网格。
提示文字是："{prompt_text}"

请分析这个验证码，告诉我应该点击哪些图片。
图片位置用行列表示，从左上角开始：
- 第1行第1列 = (1,1), 第1行第2列 = (1,2), 第1行第3列 = (1,3)
- 第2行第1列 = (2,1), 第2行第2列 = (2,2), 第2行第3列 = (2,3)
- 第3行第1列 = (3,1), 第3行第2列 = (3,2), 第3行第3列 = (3,3)

请只返回 JSON 格式，例如：
{{"positions": [[1,2], [2,3], [3,1]]}}

不要返回其他内容，只返回 JSON。''',
                    },
                ],
            }
        ],
        "max_tokens": 1024,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    result = response.json()
    return result["choices"][0]["message"]["content"]


def analyze_slider_with_markers(api_key, model, screenshot_base64, markers):
    """使用标记选择的方式识别滑块验证码"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 构建标记列表
    marker_list = ", ".join([f"{m_id}={x}px" for m_id, x in markers])

    prompt = f'''这是一个滑块验证码图片。

图片上有红色垂直标记线，每条线都有字母标识：
{marker_list}

左侧绿色框是拼图块（宽60px）。
背景中有一个缺口，形状与拼图块相同。

任务：找到缺口左边缘最接近哪个标记。

观察方法：
1. 找到背景中有明显边缘或颜色变化的矩形区域
2. 确定缺口左边缘位置
3. 选择最接近的标记字母

只返回一个字母，例如：H'''

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 16,
        "temperature": 0,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    result = response.json()
    answer = result["choices"][0]["message"]["content"].strip().upper()

    # 解析答案，找到对应的 x 坐标
    for m_id, x in markers:
        if m_id == answer:
            return {"gap_x": x, "marker": answer}

    # 如果没找到，尝试提取第一个字母
    for char in answer:
        if char.isalpha():
            for m_id, x in markers:
                if m_id == char:
                    return {"gap_x": x, "marker": char}

    return {"gap_x": 0, "marker": answer, "error": "无法解析"}


def analyze_slider_captcha(api_key, model, screenshot_base64, image_width, has_ruler=False):
    """单次 AI 识别滑块验证码"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 根据是否有刻度尺调整提示词
    if has_ruler:
        prompt = f'''这是一个滑块验证码图片。

图片说明：
- 顶部有红色像素刻度尺，标注了 0, 50, 100, 150, 200, 250, 300 等位置
- 图片上有红色垂直参考线，每 50px 一条
- 左侧绿色框（0-60px）是拼图块
- 背景中有一个缺口，形状与拼图块相同，需要找到缺口位置

任务：找到缺口的左边缘 x 坐标

方法：
1. 观察背景图，找到有明显边缘或颜色差异的矩形区域（缺口）
2. 找到缺口左边缘，看它靠近哪条红色参考线
3. 读取对应的刻度值

注意：缺口宽度约 60px，通常位于 100-250px 范围内

返回 JSON（只返回数字，不要其他文字）：
{{"gap_x": 缺口左边缘x坐标}}'''
    else:
        prompt = f'''分析这个滑块验证码图片。

图片信息：
- 总宽度：{image_width}px
- 左侧 0-60px：拼图块（puzzle piece），宽度固定 60px
- 背景中有一个缺口（gap），形状与拼图块相同

任务：找到缺口左边缘的 x 坐标（像素值）

提示：
- 缺口通常比周围区域略暗或有明显边缘
- 缺口宽度约 60px
- 缺口位置通常在 100-250px 范围内

返回 JSON 格式：
{{"gap_x": 缺口左边缘x坐标}}'''

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 128,
        "temperature": 0,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    result = response.json()
    return result["choices"][0]["message"]["content"]


def analyze_slider_hybrid(
    api_key: str,
    model: str,
    img: np.ndarray,
    puzzle_width: int = 60
) -> Dict:
    """
    混合识别：本地 CV 为主 + AI 辅助验证

    策略：
    1. 先用本地 SSIM 全图扫描找最佳位置
    2. AI 识别给出参考位置
    3. 如果 CV 得分高（>0.75），直接使用 CV 结果
    4. 如果 CV 得分低，参考 AI 位置进行二次搜索
    """
    from skimage.metrics import structural_similarity as ssim

    h, w = img.shape[:2]
    piece = img[:, :puzzle_width]

    # 1. 本地 SSIM 全图扫描
    ssim_candidates = []
    for x in range(puzzle_width + 10, w - puzzle_width):
        gap_region = img[:, x:x+puzzle_width]
        if gap_region.shape != piece.shape:
            continue
        try:
            sim = ssim(piece, gap_region, channel_axis=2)
            score = (sim + 1) / 2
            ssim_candidates.append({"x": x + puzzle_width // 2, "score": score})
        except Exception:
            pass

    ssim_candidates.sort(key=lambda c: c["score"], reverse=True)
    cv_best_x = ssim_candidates[0]["x"] if ssim_candidates else w // 2
    cv_best_score = ssim_candidates[0]["score"] if ssim_candidates else 0

    print(f"[CV] SSIM 最佳: {cv_best_x}px (score={cv_best_score:.3f})")

    # 2. AI 识别
    ruler_img = process_with_ruler(img, puzzle_width)
    base64_img = cv_image_to_base64(ruler_img)

    try:
        response = analyze_slider_captcha(api_key, model, base64_img, w, True)
        ai_data = parse_json_response(response)
        ai_gap_x = int(ai_data.get("gap_x", w // 2))
    except Exception as e:
        print(f"[AI] 识别失败: {e}")
        ai_gap_x = w // 2

    print(f"[AI] 识别位置: {ai_gap_x}px")

    # 3. 决策逻辑：CV 优先
    # 如果 CV 得分足够高，直接使用 CV 结果
    if cv_best_score >= 0.75:
        final_x = cv_best_x
        method = "cv_high_confidence"
    # 如果 AI 和 CV 结果接近，使用 CV（更精确）
    elif abs(ai_gap_x - cv_best_x) <= 30:
        final_x = cv_best_x
        method = "cv_ai_agree"
    # 如果差距大，检查 AI 位置附近是否有更好的候选
    else:
        ai_nearby = [c for c in ssim_candidates if abs(c["x"] - ai_gap_x) <= 30]
        if ai_nearby and ai_nearby[0]["score"] > cv_best_score:
            final_x = ai_nearby[0]["x"]
            method = "ai_better"
        else:
            final_x = cv_best_x
            method = "cv_fallback"

    print(f"[决策] 最终: {final_x}px (方法: {method})")

    return {
        "gap_x": final_x,
        "ai_gap_x": ai_gap_x,
        "cv_gap_x": cv_best_x,
        "cv_score": cv_best_score,
        "method": method
    }


def analyze_slider_multi_round(
    api_key: str,
    model: str,
    screenshot_bytes: bytes,
    image_width: int,
    methods: List[str] = None
) -> Dict:
    """
    多轮 AI 识别滑块验证码

    每轮使用不同的图像处理方式，综合所有结果

    参数:
        api_key: API 密钥
        model: 模型名称
        screenshot_bytes: 原始截图字节
        image_width: 图片宽度
        methods: 要使用的处理方法列表，默认使用带标注的方法

    返回:
        综合结果字典
    """
    if methods is None:
        # 默认优先使用带标注的方法
        methods = ["ruler", "grid", "original"]

    # 转换为 OpenCV 图像
    img = bytes_to_cv_image(screenshot_bytes)
    if img is None:
        return {"error": "无法解码图片"}

    results = []
    gap_x_values = []
    puzzle_x_values = []

    print(f"[AI] 开始多轮识别，共 {len(methods)} 轮...")

    for method in methods:
        if method not in IMAGE_PROCESSORS:
            continue

        name, processor = IMAGE_PROCESSORS[method]
        print(f"[AI] 第 {len(results)+1} 轮: {name}")

        try:
            # 处理图像
            processed_img = processor(img)
            processed_base64 = cv_image_to_base64(processed_img)

            # 判断是否有刻度尺
            has_ruler = method in ["ruler", "grid"]

            # 调用 AI
            ai_response = analyze_slider_captcha(api_key, model, processed_base64, image_width, has_ruler)
            ai_data = parse_json_response(ai_response)

            gap_x = int(ai_data.get("gap_x", 0))
            puzzle_x = int(ai_data.get("puzzle_x", 0))
            distance = int(ai_data.get("drag_distance", 0))

            print(f"[AI]   结果: gap_x={gap_x}, puzzle_x={puzzle_x}, distance={distance}")

            results.append({
                "method": method,
                "name": name,
                "gap_x": gap_x,
                "puzzle_x": puzzle_x,
                "distance": distance,
                "raw": ai_data
            })

            if gap_x > 0:
                gap_x_values.append(gap_x)
            if puzzle_x >= 0:
                puzzle_x_values.append(puzzle_x)

        except Exception as e:
            print(f"[AI]   失败: {e}")
            results.append({
                "method": method,
                "name": name,
                "error": str(e)
            })

    # 综合结果
    if not gap_x_values:
        return {
            "error": "所有轮次都失败",
            "results": results
        }

    # 使用投票/聚类方式确定最终结果
    final_gap_x = get_consensus_value(gap_x_values)
    final_puzzle_x = get_consensus_value(puzzle_x_values) if puzzle_x_values else 0
    final_distance = final_gap_x - final_puzzle_x

    # 计算置信度（结果一致性）
    gap_std = np.std(gap_x_values) if len(gap_x_values) > 1 else 0
    confidence = max(0, 1 - gap_std / 20)  # 标准差越小，置信度越高

    print(f"[AI] 综合结果: gap_x={final_gap_x}, puzzle_x={final_puzzle_x}, "
          f"distance={final_distance}, confidence={confidence:.2f}")
    print(f"[AI] 各轮 gap_x: {gap_x_values}, std={gap_std:.1f}")

    return {
        "gap_x": final_gap_x,
        "puzzle_x": final_puzzle_x,
        "drag_distance": final_distance,
        "confidence": confidence,
        "gap_std": gap_std,
        "all_gap_x": gap_x_values,
        "all_puzzle_x": puzzle_x_values,
        "rounds": len(results),
        "results": results
    }


def get_consensus_value(values: List[int], tolerance: int = 10) -> int:
    """
    从多个值中获取共识值

    使用聚类方式：找到最密集的区域
    """
    if not values:
        return 0

    if len(values) == 1:
        return values[0]

    # 方法1：找众数附近的值
    counter = Counter(values)
    most_common = counter.most_common(1)[0][0]

    # 收集在容差范围内的值
    nearby_values = [v for v in values if abs(v - most_common) <= tolerance]

    if nearby_values:
        # 返回这些值的中位数
        return int(np.median(nearby_values))

    # 方法2：直接返回中位数
    return int(np.median(values))


def parse_json_response(result):
    clean_result = result.strip()
    if clean_result.startswith("```"):
        lines = clean_result.split("\n")
        clean_result = "\n".join(lines[1:-1])
    return json.loads(clean_result)
