"""
滑块验证码识别模块

策略：
1. 先用 AI 识别获取大致缺口位置
2. 在 AI 结果附近用本地 CV 进行精细匹配
3. 使用多种方法综合评估，选择最可靠的结果

改进方案：
- 使用 SSIM（结构相似性）+ 改进模板匹配 + 直方图比较
- 多方法融合，动态权重
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, Dict, List

# SSIM 导入
try:
    from skimage.metrics import structural_similarity as ssim
    SSIM_AVAILABLE = True
except ImportError:
    SSIM_AVAILABLE = False
    print("[CV] 警告: scikit-image 未安装，SSIM 功能不可用")

DEBUG_DIR = Path(__file__).resolve().parents[2] / "debug"

# 币安拼图块宽度（固定值）
PUZZLE_PIECE_WIDTH = 60

# 精细搜索范围（在 AI 结果附近 ±N 像素）
FINE_SEARCH_RADIUS = 45

# 置信度阈值（相对得分，最高分与次高分的差距）
MIN_CONFIDENCE = 0.15

# 新权重配置
WEIGHTS = {
    "ssim": 0.35,           # SSIM 最可靠
    "template_v2": 0.30,    # 改进模板匹配
    "histogram": 0.20,      # 直方图辅助
    "darkness": 0.10,       # 亮度差异
    "ai_bonus": 0.05        # AI 位置奖励
}


def ensure_debug_dir():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def bytes_to_cv_image(img_bytes: bytes) -> Optional[np.ndarray]:
    """将图片字节转换为 OpenCV 图像"""
    if img_bytes is None:
        return None
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    return img


def extract_background_image(page) -> Optional[bytes]:
    """从页面提取背景图"""
    selectors = [
        ".bs-main-image",
        "[class*='slider-bg']",
        "[class*='captcha-bg']",
        ".bcap-bg",
        "[class*='verify-img']",
        "[class*='main-image']",
    ]
    for selector in selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                print(f"[CV] 找到背景图: {selector}")
                return el.screenshot()
        except Exception:
            pass
    return None


# ============ 新增检测方法 ============

def detect_gap_by_ssim(img: np.ndarray, puzzle_width: int = 60) -> List[Dict]:
    """
    使用 SSIM 结构相似性检测缺口
    拼图块有完整纹理，在背景中找到纹理最相似的区域
    """
    if not SSIM_AVAILABLE:
        return []

    h, w = img.shape[:2]
    piece = img[:, :puzzle_width]

    candidates = []
    for x in range(puzzle_width + 10, w - puzzle_width):
        gap_region = img[:, x:x+puzzle_width]
        if gap_region.shape == piece.shape:
            try:
                sim = ssim(piece, gap_region, channel_axis=2)
                candidates.append({
                    "gap_x": x + puzzle_width // 2,
                    "score": (sim + 1) / 2,  # 归一化到 0-1
                    "method": "ssim"
                })
            except Exception:
                pass

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:20]


def detect_gap_by_template_v2(img: np.ndarray, puzzle_width: int = 60) -> List[Dict]:
    """
    改进模板匹配：使用原始纹理而非边缘
    多方法融合取平均
    """
    h, w = img.shape[:2]
    piece = img[:, :puzzle_width]
    bg_search = img[:, puzzle_width:]

    if bg_search.shape[1] < piece.shape[1]:
        return []

    # 多方法融合
    results = []
    for method in [cv2.TM_CCORR_NORMED, cv2.TM_CCOEFF_NORMED]:
        try:
            result = cv2.matchTemplate(bg_search, piece, method)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            gap_x = max_loc[0] + puzzle_width + puzzle_width // 2
            results.append({"gap_x": gap_x, "score": max_val})
        except Exception:
            pass

    if not results:
        return []

    avg_gap_x = int(np.mean([r["gap_x"] for r in results]))
    avg_score = np.mean([r["score"] for r in results])

    return [{"gap_x": avg_gap_x, "score": avg_score, "method": "template_v2"}]


def detect_gap_by_histogram(img: np.ndarray, puzzle_width: int = 60) -> List[Dict]:
    """
    使用直方图比较检测缺口
    比较拼图块和背景各区域的颜色分布相似度
    """
    h, w = img.shape[:2]
    piece = img[:, :puzzle_width]

    # 计算拼图块的颜色直方图
    piece_hist = cv2.calcHist([piece], [0, 1, 2], None, [8, 8, 8],
                              [0, 256, 0, 256, 0, 256])
    piece_hist = cv2.normalize(piece_hist, piece_hist).flatten()

    candidates = []
    for x in range(puzzle_width + 10, w - puzzle_width):
        gap_region = img[:, x:x+puzzle_width]
        gap_hist = cv2.calcHist([gap_region], [0, 1, 2], None, [8, 8, 8],
                               [0, 256, 0, 256, 0, 256])
        gap_hist = cv2.normalize(gap_hist, gap_hist).flatten()

        # Bhattacharyya 距离（越小越相似）
        dist = cv2.compareHist(piece_hist, gap_hist, cv2.HISTCMP_BHATTACHARYYA)
        similarity = 1 / (1 + dist)

        candidates.append({
            "gap_x": x + puzzle_width // 2,
            "score": similarity,
            "method": "histogram"
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:20]


def detect_gap_by_gradient(img: np.ndarray, puzzle_width: int = 60) -> List[Dict]:
    """
    通过梯度分析检测缺口位置
    缺口区域通常有明显的垂直边缘
    """
    h, w = img.shape[:2]

    # 转灰度
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    # 计算水平方向梯度（检测垂直边缘）
    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_x = np.abs(sobel_x)

    # 只分析背景区域（跳过左侧拼图块）
    bg_start = puzzle_width + 10

    # 计算每列的梯度强度
    col_gradients = []
    for x in range(bg_start, w - puzzle_width):
        # 取该位置附近的梯度
        region = sobel_x[:, x:x+5]
        gradient_sum = np.sum(region)
        col_gradients.append({
            "x": x,
            "gradient": gradient_sum
        })

    # 按梯度排序，找到梯度最强的位置（可能是缺口边缘）
    col_gradients.sort(key=lambda x: x["gradient"], reverse=True)

    # 返回前几个候选位置
    candidates = []
    for item in col_gradients[:20]:
        candidates.append({
            "gap_x": item["x"],
            "score": item["gradient"] / col_gradients[0]["gradient"] if col_gradients else 0,
            "method": "gradient"
        })

    return candidates


def detect_gap_by_darkness(img: np.ndarray, puzzle_width: int = 60) -> List[Dict]:
    """
    通过亮度分析检测缺口位置
    缺口区域通常比周围更暗（有阴影）
    """
    h, w = img.shape[:2]

    # 转灰度
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    bg_start = puzzle_width + 10

    # 计算每个位置的平均亮度
    candidates = []
    for x in range(bg_start, w - puzzle_width):
        region = gray[:, x:x+puzzle_width]
        mean_brightness = np.mean(region)
        candidates.append({
            "gap_x": x + puzzle_width // 2,
            "brightness": mean_brightness
        })

    # 找到最暗的区域
    if not candidates:
        return []

    min_brightness = min(c["brightness"] for c in candidates)
    max_brightness = max(c["brightness"] for c in candidates)
    brightness_range = max_brightness - min_brightness

    if brightness_range == 0:
        return []

    # 归一化得分（越暗得分越高）
    result = []
    for c in candidates:
        score = (max_brightness - c["brightness"]) / brightness_range
        result.append({
            "gap_x": c["gap_x"],
            "score": score,
            "method": "darkness"
        })

    result.sort(key=lambda x: x["score"], reverse=True)
    return result[:20]


def detect_gap_by_template(img: np.ndarray, puzzle_width: int = 60) -> List[Dict]:
    """
    使用模板匹配检测缺口位置
    将拼图块作为模板，在背景中搜索最匹配的位置
    """
    h, w = img.shape[:2]

    # 提取拼图块区域
    piece = img[:, :puzzle_width]

    # 转灰度
    if len(img.shape) == 3:
        piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
        bg_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        piece_gray = piece
        bg_gray = img

    # 边缘检测
    piece_edges = cv2.Canny(piece_gray, 100, 200)
    bg_edges = cv2.Canny(bg_gray, 100, 200)

    # 只在背景区域搜索
    bg_search = bg_edges[:, puzzle_width:]

    # 模板匹配
    result = cv2.matchTemplate(bg_search, piece_edges, cv2.TM_CCOEFF_NORMED)

    # 获取所有匹配位置
    candidates = []
    for x in range(result.shape[1]):
        score = result[0, x] if result.shape[0] == 1 else np.max(result[:, x])
        candidates.append({
            "gap_x": x + puzzle_width + puzzle_width // 2,
            "score": float(score),
            "method": "template"
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:20]


def detect_gap_by_contour(img: np.ndarray, puzzle_width: int = 60) -> List[Dict]:
    """
    通过轮廓检测找缺口
    缺口区域通常有明显的矩形轮廓
    """
    h, w = img.shape[:2]

    # 转灰度
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    # 只分析背景区域
    bg = gray[:, puzzle_width:]

    # 边缘检测
    edges = cv2.Canny(bg, 50, 150)

    # 膨胀边缘使轮廓更连续
    kernel = np.ones((3, 3), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)

    # 找轮廓
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 500:  # 过滤太小的轮廓
            continue

        x, y, cw, ch = cv2.boundingRect(contour)

        # 检查是否接近拼图块尺寸
        if 0.5 < cw / puzzle_width < 1.5 and 0.5 < ch / h < 1.5:
            center_x = x + cw // 2 + puzzle_width
            candidates.append({
                "gap_x": center_x,
                "score": area / (puzzle_width * h),
                "method": "contour",
                "area": area
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:10]


def fine_tune_position(
    img: np.ndarray,
    ai_gap_x: int,
    puzzle_width: int = 60,
    search_radius: int = 40,
    debug_prefix: str = ""
) -> Tuple[int, float, List[Dict]]:
    """
    在 AI 识别位置附近进行精细搜索
    使用改进的多方法融合：SSIM + 改进模板匹配 + 直方图 + 亮度
    """
    h, w = img.shape[:2]

    # 使用新方法检测
    ssim_candidates = detect_gap_by_ssim(img, puzzle_width)
    template_v2_candidates = detect_gap_by_template_v2(img, puzzle_width)
    histogram_candidates = detect_gap_by_histogram(img, puzzle_width)
    darkness_candidates = detect_gap_by_darkness(img, puzzle_width)

    # 搜索范围（确保包含 ai_gap_x）
    start_x = max(puzzle_width, ai_gap_x - search_radius)
    end_x = min(w - puzzle_width // 2, ai_gap_x + search_radius)

    # 为每个位置计算综合得分
    position_scores = {}

    def add_score(gap_x, score, weight, method):
        # 放宽范围限制，允许所有候选位置参与评分
        if puzzle_width <= gap_x <= w - puzzle_width // 2:
            if gap_x not in position_scores:
                position_scores[gap_x] = {"total": 0, "count": 0, "details": {}}
            position_scores[gap_x]["total"] += score * weight
            position_scores[gap_x]["count"] += weight
            position_scores[gap_x]["details"][method] = score

    # 添加各方法的得分（使用新权重）
    for c in ssim_candidates[:15]:
        add_score(c["gap_x"], c["score"], WEIGHTS["ssim"], "ssim")

    for c in template_v2_candidates[:5]:
        add_score(c["gap_x"], c["score"], WEIGHTS["template_v2"], "template_v2")

    for c in histogram_candidates[:15]:
        add_score(c["gap_x"], c["score"], WEIGHTS["histogram"], "histogram")

    for c in darkness_candidates[:10]:
        add_score(c["gap_x"], c["score"], WEIGHTS["darkness"], "darkness")

    # AI 位置加权（给 AI 结果附近的位置额外加分）
    for gap_x in position_scores.keys():
        distance_to_ai = abs(gap_x - ai_gap_x)
        if distance_to_ai <= search_radius:
            ai_bonus = max(0, 1 - distance_to_ai / search_radius) * WEIGHTS["ai_bonus"]
            position_scores[gap_x]["total"] += ai_bonus
            position_scores[gap_x]["count"] += WEIGHTS["ai_bonus"]

    # 计算最终得分
    candidates = []
    for gap_x, data in position_scores.items():
        if data["count"] > 0:
            avg_score = data["total"] / data["count"]
            candidates.append({
                "gap_x": gap_x,
                "score": avg_score,
                "raw_total": data["total"],
                "weight_sum": data["count"],
                "details": data["details"]
            })

    if not candidates:
        return ai_gap_x, 0.0, []

    # 按得分排序
    candidates.sort(key=lambda x: x["score"], reverse=True)

    best = candidates[0]

    # 计算置信度（最高分与第二高分的差距）
    if len(candidates) > 1:
        confidence = best["score"] - candidates[1]["score"]
    else:
        confidence = best["score"]

    # 保存调试信息
    if debug_prefix:
        ensure_debug_dir()
        debug_img = img.copy()

        # 画各方法的检测结果
        colors = {
            "ssim": (0, 255, 255),       # 黄
            "template_v2": (0, 255, 0),  # 绿
            "histogram": (255, 0, 255),  # 紫
            "darkness": (255, 255, 0),   # 青
        }

        for method, cands in [
            ("ssim", ssim_candidates[:3]),
            ("template_v2", template_v2_candidates[:3]),
            ("histogram", histogram_candidates[:3]),
            ("darkness", darkness_candidates[:3])
        ]:
            for c in cands:
                x = c["gap_x"]
                cv2.line(debug_img, (x, 0), (x, h), colors.get(method, (128, 128, 128)), 1)

        # 画最终结果
        cv2.line(debug_img, (best["gap_x"], 0), (best["gap_x"], h), (0, 0, 255), 2)

        cv2.imwrite(str(DEBUG_DIR / f"{debug_prefix}_methods.png"), debug_img)

    return best["gap_x"], best["score"], candidates[:10]


def solve_slider_with_cv(
    page,
    debug_prefix: str = "",
    ai_gap_x: int = None,
    ai_puzzle_x: int = None
) -> Optional[Dict]:
    """
    使用 AI + 本地精细匹配求解滑块验证码
    """
    print("[CV] 开始本地精细匹配...")

    # 1. 提取背景图
    bg_bytes = extract_background_image(page)
    if bg_bytes is None:
        print("[CV] 无法提取背景图")
        return None

    combined_img = bytes_to_cv_image(bg_bytes)
    if combined_img is None:
        print("[CV] 背景图解码失败")
        return None

    h, w = combined_img.shape[:2]
    print(f"[CV] 图像尺寸: {w}x{h}")

    # 保存原始图像
    if debug_prefix:
        ensure_debug_dir()
        cv2.imwrite(str(DEBUG_DIR / f"{debug_prefix}_bg.png"), combined_img)

    # 2. 检查是否有 AI 识别结果
    if ai_gap_x is None:
        print("[CV] 警告: 没有 AI 识别结果，使用图像中心作为初始位置")
        ai_gap_x = w // 2

    print(f"[CV] AI 识别位置: gap_x={ai_gap_x}")

    # 3. 在 AI 结果附近进行精细搜索
    best_gap_x, best_score, candidates = fine_tune_position(
        combined_img,
        ai_gap_x,
        PUZZLE_PIECE_WIDTH,
        FINE_SEARCH_RADIUS,
        debug_prefix
    )

    print(f"[CV] 精细匹配结果: gap_x={best_gap_x}, score={best_score:.4f}")

    # 4. 打印前几个候选结果
    if candidates:
        print("[CV] 候选位置:")
        for i, c in enumerate(candidates[:5]):
            print(f"  #{i+1}: gap_x={c['gap_x']}, score={c['score']:.3f}")

    # 5. 计算滑动距离
    slider_x = ai_puzzle_x if ai_puzzle_x is not None else PUZZLE_PIECE_WIDTH // 2
    distance = int(best_gap_x - slider_x)

    print(f"[CV] 滑动距离: {distance}px (gap={best_gap_x} - slider={slider_x})")

    # 6. 生成验证图像
    if debug_prefix:
        result_img = combined_img.copy()

        # 画 AI 识别位置（蓝线）
        cv2.line(result_img, (ai_gap_x, 0), (ai_gap_x, h), (255, 0, 0), 1)

        # 画精细匹配位置（绿线）
        cv2.line(result_img, (best_gap_x, 0), (best_gap_x, h), (0, 255, 0), 2)

        # 画缺口区域（红框）
        gap_left = best_gap_x - PUZZLE_PIECE_WIDTH // 2
        cv2.rectangle(result_img, (gap_left, 0), (gap_left + PUZZLE_PIECE_WIDTH, h), (0, 0, 255), 2)

        # 标注
        cv2.putText(result_img,
                   f"AI:{ai_gap_x} -> CV:{best_gap_x} dist={distance} score={best_score:.3f}",
                   (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

        cv2.imwrite(str(DEBUG_DIR / f"{debug_prefix}_result.png"), result_img)

    # 7. 判断是否可信
    # 使用相对置信度而非绝对阈值
    verified = best_score >= MIN_CONFIDENCE

    if not verified:
        print(f"[CV] 警告: 置信度 {best_score:.3f} 低于阈值 {MIN_CONFIDENCE}")

    return {
        "gap_x": best_gap_x,
        "slider_x": slider_x,
        "distance": distance,
        "score": best_score,
        "method": "ai_cv_hybrid",
        "verified": verified,
        "ai_gap_x": ai_gap_x,
        "candidates": candidates[:5] if candidates else []
    }


def refine_distance_with_local_sim(
    page,
    initial_distance: int,
    search_radius: int = 15,
    step: int = 1,
    debug_prefix: str = ""
) -> Tuple[int, float]:
    """
    在初始距离附近精细搜索（保留接口兼容性）
    """
    return initial_distance, 0.5


# ============ 像素级拟合验证 ============

# 像素级验证阈值 - SSIM 得分阈值
# 注意：这是归一化后的值，0.80 对应原始 SSIM 0.60
PIXEL_FIT_THRESHOLD = 0.80

# 初始搜索范围（在 AI 位置附近搜索）
PIXEL_SEARCH_RADIUS_INIT = 20
# 每次扩大的步长
PIXEL_SEARCH_RADIUS_STEP = 15
# 最大搜索范围
PIXEL_SEARCH_RADIUS_MAX = 150


def detect_gap_position(img: np.ndarray, puzzle_width: int = 60) -> Tuple[int, float]:
    """
    通过边缘检测找到缺口位置
    缺口区域通常有明显的垂直边缘（左右两侧）

    Returns:
        (缺口中心 x 坐标, 置信���得分)
    """
    h, w = img.shape[:2]

    # 转灰度
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # 边缘检测
    edges = cv2.Canny(gray, 100, 200)

    # 只分析背景区域（跳过左侧拼图块）
    bg_start = puzzle_width + 10

    # 计算每列的边缘强度
    col_scores = []
    for x in range(bg_start, w - puzzle_width):
        # 检查该位置是否有垂直边缘（缺口的左边缘）
        left_edge = edges[:, x:x+3]
        left_score = np.sum(left_edge) / (h * 3 * 255)

        # 检查 puzzle_width 像素后是否有垂直边缘（缺口的右边缘）
        right_x = x + puzzle_width
        if right_x + 3 <= w:
            right_edge = edges[:, right_x:right_x+3]
            right_score = np.sum(right_edge) / (h * 3 * 255)
        else:
            right_score = 0

        # 综合得分：左右边缘都要有
        score = (left_score + right_score) / 2
        col_scores.append((x + puzzle_width // 2, score))

    if not col_scores:
        return w // 2, 0.0

    # 找到得分最高的位置
    col_scores.sort(key=lambda x: x[1], reverse=True)
    best_x, best_score = col_scores[0]

    return best_x, best_score


def detect_gap_by_darkness_local(img: np.ndarray, center_x: int, puzzle_width: int = 60, search_range: int = 30) -> Tuple[int, float]:
    """
    在指定位置附近通过亮度差异精确定位缺口
    缺口区域通常比周围更暗（有阴影或颜色差异）

    Returns:
        (缺口中心 x 坐标, 置信度得分)
    """
    h, w = img.shape[:2]

    # 转灰度
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # 搜索范围
    start_x = max(puzzle_width, center_x - search_range)
    end_x = min(w - puzzle_width, center_x + search_range)

    best_x = center_x
    best_diff = 0.0

    for x in range(start_x, end_x + 1):
        # 缺口区域
        gap_left = x - puzzle_width // 2
        gap_right = gap_left + puzzle_width
        gap_region = gray[:, gap_left:gap_right]

        # 左侧参考区域
        left_ref = gray[:, max(0, gap_left - 20):gap_left]
        # 右侧参考区域
        right_ref = gray[:, gap_right:min(w, gap_right + 20)]

        if left_ref.size == 0 or right_ref.size == 0:
            continue

        # 计算亮度差异
        gap_mean = np.mean(gap_region)
        ref_mean = (np.mean(left_ref) + np.mean(right_ref)) / 2
        diff = abs(gap_mean - ref_mean)

        if diff > best_diff:
            best_diff = diff
            best_x = x

    # 归一化得分
    score = min(1.0, best_diff / 50.0)

    return best_x, score


def verify_gap_fit(img: np.ndarray, gap_x: int, puzzle_width: int = 60) -> Tuple[bool, float]:
    """
    改进的拟合验证 - 使用 SSIM 作为主要指标

    SSIM 直接比较拼图块和缺口区域的结构相似性，
    是最可靠的匹配指标。

    Returns:
        (是否通过验证, SSIM 得分)
    """
    h, w = img.shape[:2]

    # 计算缺口区域边界
    gap_left = gap_x - puzzle_width // 2
    gap_left = max(0, min(gap_left, w - puzzle_width))

    # 提取拼图块和缺口区域
    piece = img[:, :puzzle_width]
    gap_region = img[:, gap_left:gap_left+puzzle_width]

    # 确保尺寸匹配
    if piece.shape != gap_region.shape:
        return False, 0.0

    # 使用 SSIM 作为唯一评分标准
    if SSIM_AVAILABLE:
        try:
            sim = ssim(piece, gap_region, channel_axis=2 if len(piece.shape) == 3 else None)
            # SSIM 范围是 [-1, 1]，归一化到 [0, 1]
            score = (sim + 1) / 2
        except Exception:
            score = 0.0
    else:
        score = 0.0

    # 阈值判断：SSIM >= 0.95 对应原始值 >= 0.90
    passed = score >= PIXEL_FIT_THRESHOLD
    return passed, score


def verify_pixel_fit(
    img: np.ndarray,
    gap_x: int,
    puzzle_width: int = 60,
    threshold: float = PIXEL_FIT_THRESHOLD,
    debug_prefix: str = ""
) -> Tuple[bool, float]:
    """
    验证拟合度
    """
    passed, score = verify_gap_fit(img, gap_x, puzzle_width)
    return score >= threshold, score


def search_best_pixel_fit(
    img: np.ndarray,
    initial_gap_x: int,
    puzzle_width: int = 60,
    search_radius: int = PIXEL_SEARCH_RADIUS_INIT,
    debug_prefix: str = ""
) -> Tuple[int, float]:
    """
    在候选位置附近使用模板匹配搜索最佳拟合位置

    Args:
        img: 背景图像
        initial_gap_x: 初始缺口位置（AI 识别结果）
        puzzle_width: 拼图块宽度
        search_radius: 搜索范围（±N 像素）
        debug_prefix: 调试文件前缀

    Returns:
        (最佳 gap_x, 匹配得分)
    """
    h, w = img.shape[:2]

    # 提取拼图块区域
    piece = img[:, :puzzle_width]

    # 转灰度
    if len(img.shape) == 3:
        piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
        bg_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        piece_gray = piece
        bg_gray = img

    # 边缘检测
    piece_edges = cv2.Canny(piece_gray, 100, 200)
    bg_edges = cv2.Canny(bg_gray, 100, 200)

    # 搜索范围
    start_x = max(puzzle_width, initial_gap_x - search_radius)
    end_x = min(w - puzzle_width, initial_gap_x + search_radius)

    # 在搜索范围内进行模板匹配
    search_region = bg_edges[:, start_x:end_x + puzzle_width]

    if search_region.shape[1] < piece_edges.shape[1]:
        print(f"[像素搜索] 搜索区域太小，使用初始位置")
        return initial_gap_x, 0.5

    # 模板匹配
    result = cv2.matchTemplate(search_region, piece_edges, cv2.TM_CCOEFF_NORMED)

    # 找到最佳匹配位置
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

    # 计算实际的 gap_x（缺口中心位置）
    best_gap_x = start_x + max_loc[0] + puzzle_width // 2

    print(f"[像素搜索] 在 {initial_gap_x} ±{search_radius}px 范围内搜索...")
    print(f"[像素搜索] 最佳位置: gap_x={best_gap_x}, 匹配得分={max_val:.3f}")

    # 保存调试信息
    if debug_prefix:
        ensure_debug_dir()

        debug_img = img.copy()

        # 画搜索范围
        cv2.rectangle(debug_img, (start_x, 0), (end_x + puzzle_width, h), (255, 255, 0), 1)

        # 画 AI 位置（蓝线）
        cv2.line(debug_img, (initial_gap_x, 0), (initial_gap_x, h), (255, 0, 0), 1)

        # 画最佳位置（绿线）
        cv2.line(debug_img, (best_gap_x, 0), (best_gap_x, h), (0, 255, 0), 2)

        # 画缺口区域（红框）
        gap_left = best_gap_x - puzzle_width // 2
        cv2.rectangle(debug_img, (gap_left, 0), (gap_left + puzzle_width, h), (0, 0, 255), 2)

        # 标注
        cv2.putText(debug_img, f"AI:{initial_gap_x} -> best:{best_gap_x} score={max_val:.3f}",
                   (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

        cv2.imwrite(str(DEBUG_DIR / f"{debug_prefix}_pixel_search.png"), debug_img)

    return best_gap_x, max_val


def pixel_verified_solve(
    img: np.ndarray,
    ai_gap_x: int,
    slider_x: int,
    puzzle_width: int = 60,
    debug_prefix: str = ""
) -> Optional[Dict]:
    """
    基于 AI 识别位置进行本地精确拟合

    核心原理：拼图块是从缺口位置挖出来的，颜色分布必然一致。
    使用颜色直方图相关性作为主要匹配指标。

    策略：
    1. AI 给出大致范围（±60px）
    2. 颜色直方图相关性精确定位（权重 80%）
    3. 边缘对检测辅助（权重 10%）
    4. AI 距离约束（权重 10%）

    Args:
        img: 背景图像
        ai_gap_x: AI 识别的缺口位置（中心）
        slider_x: 滑块初始位置
        puzzle_width: 拼图块宽度
        debug_prefix: 调试文件前缀

    Returns:
        验证结果字典
    """
    h, w = img.shape[:2]
    piece = img[:, :puzzle_width]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()

    # 拼图块颜色直方图（16 bins 精细匹配）
    piece_hist = cv2.calcHist([piece], [0, 1, 2], None, [16, 16, 16],
                              [0, 256, 0, 256, 0, 256])
    piece_hist = cv2.normalize(piece_hist, piece_hist).flatten()

    # Sobel 梯度（检测垂直边缘）
    sobel_x = np.abs(cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3))
    col_gradients = np.sum(sobel_x, axis=0)
    max_gradient = np.max(col_gradients) if np.max(col_gradients) > 0 else 1

    # 搜索范围：AI 位置 ±60px
    search_radius = 60
    ai_left = ai_gap_x - puzzle_width // 2
    start = max(puzzle_width + 5, ai_left - search_radius)
    end = min(w - puzzle_width, ai_left + search_radius)

    print(f"[本地拟合] AI 位置 {ai_gap_x} ±{search_radius}px 范围搜索 (直方图+边缘+AI距离)...")

    best_x = ai_left
    best_score = -1
    best_corr = 0

    for x in range(start, end + 1):
        region = img[:, x:x+puzzle_width]

        # 1. 颜色直方图相关性（80%）
        region_hist = cv2.calcHist([region], [0, 1, 2], None, [16, 16, 16],
                                   [0, 256, 0, 256, 0, 256])
        region_hist = cv2.normalize(region_hist, region_hist).flatten()
        corr = cv2.compareHist(piece_hist, region_hist, cv2.HISTCMP_CORREL)

        # 2. 边缘对得分（10%）- 缺口左右边缘都应有垂直边缘
        left_grad = np.mean(col_gradients[max(0, x-2):x+3]) / max_gradient
        right_grad = np.mean(col_gradients[max(0, x+puzzle_width-2):min(w, x+puzzle_width+3)]) / max_gradient
        edge_score = min(left_grad, right_grad)

        # 3. AI 距离惩罚（10%）- 离 AI 位置越远惩罚越大
        dist = abs((x + puzzle_width // 2) - ai_gap_x)
        ai_proximity = 1 - dist / search_radius

        # 综合得分
        total = corr * 0.80 + edge_score * 0.10 + ai_proximity * 0.10

        if total > best_score:
            best_score = total
            best_x = x
            best_corr = corr

    best_center = best_x + puzzle_width // 2
    verified = best_corr >= 0.70

    print(f"[本地拟合] 结果: gap_x={best_center}, corr={best_corr:.3f}, verified={verified}")

    # 保存调试图像
    if debug_prefix:
        ensure_debug_dir()
        debug_img = img.copy()
        cv2.line(debug_img, (ai_gap_x, 0), (ai_gap_x, h), (255, 0, 0), 1)
        cv2.line(debug_img, (best_center, 0), (best_center, h), (0, 255, 0), 2)
        cv2.rectangle(debug_img, (best_x, 0), (best_x + puzzle_width, h), (0, 0, 255), 2)
        cv2.putText(debug_img, f"AI:{ai_gap_x} -> fit:{best_center} corr={best_corr:.3f}",
                   (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        cv2.imwrite(str(DEBUG_DIR / f"{debug_prefix}_fit_result.png"), debug_img)

    return {
        "gap_x": best_center,
        "distance": best_center - slider_x,
        "pixel_overlap": best_corr,
        "pixel_verified": verified,
        "ai_gap_x": ai_gap_x,
    }

    return {
        "gap_x": best_gap_x,
        "distance": best_gap_x - slider_x,
        "pixel_overlap": best_overlap,
        "pixel_verified": verified,
        "ai_gap_x": ai_gap_x,
        "search_radius": PIXEL_SEARCH_RADIUS_MAX
    }