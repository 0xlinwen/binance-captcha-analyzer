import random
import time
import json
import fcntl
from pathlib import Path

from .captcha_ai import (
    analyze_click_captcha,
    analyze_slider_captcha,
    parse_json_response,
    screenshot_to_base64,
)

# OpenCV 本地匹配模块
try:
    from .slider_cv import (
        pixel_verified_solve,
        bytes_to_cv_image,
        extract_background_image,
        PUZZLE_PIECE_WIDTH,
    )
    CV_AVAILABLE = True
except ImportError:
    CV_AVAILABLE = False
    print("[WARNING] OpenCV 模块未安装，将仅使用 AI 识别")

SLIDER_OFFSET_PX = 0
FAST_DRAG_STEPS = 2
MAX_COOLDOWN_SECONDS = 5
SLIDER_OFFSET_MIN = -30
SLIDER_OFFSET_MAX = 30
# 本地 CV 匹配的置信度阈值
CV_CONFIDENCE_THRESHOLD = 0.3
SLIDER_OFFSET_FILE = Path(__file__).resolve().parents[2] / "output" / "slider_offset.json"
SLIDER_OFFSET_LOCK = Path(__file__).resolve().parents[2] / "output" / "slider_offset.lock"


def _load_learned_slider_offset():
    try:
        if not SLIDER_OFFSET_FILE.exists():
            return 0
        data = json.loads(SLIDER_OFFSET_FILE.read_text(encoding="utf-8"))
        return int(data.get("offset_px", 0))
    except Exception:
        return 0


def _save_learned_slider_offset(offset_px):
    offset_px = max(SLIDER_OFFSET_MIN, min(SLIDER_OFFSET_MAX, int(offset_px)))
    SLIDER_OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SLIDER_OFFSET_LOCK, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            payload = {"offset_px": offset_px}
            SLIDER_OFFSET_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _captcha_gone_stably(page, checks=3, interval_ms=250):
    """Confirm captcha is absent across multiple checks to avoid transient false positives."""
    for idx in range(max(1, checks)):
        captcha_type, _ = detect_captcha_type(page)
        if captcha_type != "unknown":
            return False
        if idx < checks - 1:
            page.wait_for_timeout(interval_ms)
    return True


def simulate_human_drag(page, slider_element, distance):
    """模拟人类滑动行为"""
    try:
        box = slider_element.bounding_box()
        if not box:
            print("[滑动] 错误: 无法获取滑块位置")
            return False

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        end_x = start_x + distance

        print(f"[滑动] 起点: ({start_x:.1f}, {start_y:.1f}), 终点: ({end_x:.1f}, {start_y:.1f}), 距离: {distance}px")

        # 移动到滑块
        page.mouse.move(start_x, start_y)
        time.sleep(random.uniform(0.1, 0.2))
        print(f"[滑动] 鼠标已移动到滑块")

        # 按下鼠标
        page.mouse.down()
        time.sleep(random.uniform(0.05, 0.1))
        print(f"[滑动] 鼠标已按下")

        # 分步滑动
        steps = random.randint(20, 30)
        for i in range(steps):
            progress = (i + 1) / steps
            eased = progress * (2 - progress)  # 缓动函数
            target_x = start_x + distance * eased
            jitter_y = random.uniform(-0.5, 0.5)
            page.mouse.move(target_x, start_y + jitter_y)
            time.sleep(random.uniform(0.01, 0.03))

        # 最终位置
        page.mouse.move(start_x + distance, start_y)
        time.sleep(random.uniform(0.1, 0.15))
        print(f"[滑动] 已滑动到目标位置")

        # 释放鼠标
        page.mouse.up()
        print(f"[滑动] 鼠标已释放")

        # 等待验证码系统处理
        wait_time = random.uniform(1.0, 3.0)
        print(f"[滑动] 等待验证 {wait_time:.1f}秒...")
        time.sleep(wait_time)

        return True
    except Exception as e:
        print(f"[滑动] 异常: {e}")
        import traceback
        print(traceback.format_exc())
        return False


def simulate_fast_drag_to_target(page, slider_element, distance):
    """Fast drag directly to target x with minimal intermediate points."""
    box = slider_element.bounding_box()
    if not box:
        return False

    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    target_x = start_x + max(0, distance)

    page.mouse.move(start_x, start_y)
    time.sleep(0.03)
    page.mouse.down()
    time.sleep(0.02)

    # Very short route to reduce latency.
    for i in range(FAST_DRAG_STEPS):
        t = (i + 1) / FAST_DRAG_STEPS
        page.mouse.move(start_x + (target_x - start_x) * t, start_y)
        time.sleep(0.01)

    page.mouse.up()
    return True


def detect_captcha_type(page):
    click_modal = page.query_selector(".bcap-modal")
    if click_modal and click_modal.is_visible():
        return "click", click_modal

    slider_selectors = [
        ".bs-modal",
        ".bs-slide-container",
        ".verify-slider",
        ".bcap-slider",
        ".bcap-drag",
        "[class*='slider-container']",
        "[class*='slide-verify']",
        "[class*='captcha-slider']",
        ".geetest_slider",
    ]
    for selector in slider_selectors:
        slider = page.query_selector(selector)
        if slider and slider.is_visible():
            return "slider", slider

    popup = page.query_selector(".bcapc-popup, .bs-popup")
    if popup and popup.is_visible():
        return "slider", popup

    return "unknown", None


def dismiss_global_modal(page):
    """Dismiss blocking global modal in current page only."""
    try:
        modal = page.query_selector("#globalmodal-common")
        if not modal or not modal.is_visible():
            return False
    except Exception:
        return False

    selectors = [
        "#globalmodal-common button:has-text('已知晓')",
        "#globalmodal-common button:has-text('确定')",
        "#globalmodal-common button:has-text('关闭')",
        "#globalmodal-common button:has-text('OK')",
        "#globalmodal-common button:has-text('Got it')",
        "#globalmodal-common button:has-text('Close')",
        "#globalmodal-common [aria-label='Close']",
        "#globalmodal-common .close",
    ]
    for selector in selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click(timeout=2500, force=True)
                page.wait_for_timeout(300)
                print(f"关闭全局弹窗: {selector}")
                return True
        except Exception:
            pass

    try:
        page.evaluate(
            """
            () => {
              const n = document.querySelector('#globalmodal-common');
              if (!n) return false;
              n.style.display = 'none';
              n.style.pointerEvents = 'none';
              return true;
            }
            """
        )
        page.wait_for_timeout(150)
        print("通过注入样式隐藏全局弹窗")
        return True
    except Exception:
        return False


def click_captcha_images(page, positions, click_retry_per_cell=3):
    """Click cells in currently visible captcha container to avoid stale/intercepted elements."""
    clicked = []
    dismiss_global_modal(page)

    def get_container():
        return (
            page.query_selector("#globalmodal-common .bcap-modal")
            or page.query_selector("#globalmodal-common .bcapc-popup")
            or page.query_selector(".bcap-modal")
            or page.query_selector(".bcapc-popup")
        )

    container = get_container()
    if not container:
        print("[ERROR] 未找到验证码容器")
        return clicked

    container_box = container.bounding_box()

    for row, col in positions:
        full_selector = f".bcap-modal .bcap-image{row}{col}, .bcapc-popup .bcap-image{row}{col}"
        success = False
        last_err = "element_not_clickable"

        for attempt in range(max(1, click_retry_per_cell)):
            try:
                elements = page.query_selector_all(full_selector)

                valid_element = None
                container = get_container()
                container_box = container.bounding_box() if container else None

                if not container_box:
                    last_err = "container_not_found"
                    time.sleep(0.2)
                    continue

                for elem in elements:
                    if not elem.is_visible():
                        continue
                    box = elem.bounding_box()
                    if not box:
                        continue

                    elem_center_x = box["x"] + box["width"] / 2
                    elem_center_y = box["y"] + box["height"] / 2

                    if (container_box["x"] <= elem_center_x <= container_box["x"] + container_box["width"] and
                        container_box["y"] <= elem_center_y <= container_box["y"] + container_box["height"]):
                        valid_element = elem
                        break

                if not valid_element:
                    time.sleep(0.2)
                    continue

                valid_element.click(timeout=3000)

                clicked.append((row, col))
                print(f"  点击了位置 ({row},{col})")
                time.sleep(random.uniform(0.25, 0.45))
                success = True
                break
            except Exception as e:
                last_err = str(e)[:50]
                time.sleep(random.uniform(0.2, 0.35))

        if not success:
            print(f"  点击位置 ({row},{col}) 失败: {last_err}")

    return clicked


def solve_captcha(
    page,
    api_key,
    model,
    max_attempts=3,
    email_addr="",
    retry_mode="fast",
    max_rounds=1,
    reload_url=None,
    page_timeout=60000,
    cooldown_min_sec=20,
    cooldown_max_sec=60,
    click_retry_per_cell=3,
    auto_tune_slider_offset=True,
):
    model_candidates = model if isinstance(model, (list, tuple)) else [model]
    model_candidates = [m for m in model_candidates if m]
    if not model_candidates:
        raise ValueError("models 配置为空，无法识别验证码")
    primary_model = model_candidates[0]
    fast_mode = retry_mode == "fast"
    auto_tune = bool(auto_tune_slider_offset)
    learned_offset = _load_learned_slider_offset() if auto_tune else 0
    rate_limit_signatures = [
        "too_many_attempts",
        "尝试次数过多",
        "cap_too_many",
        "cap_too_many_attempts",
        "208075",
        "认证失败，请刷新页面后重试",
        "$e.execute is not a function",
    ]

    for round_idx in range(max_rounds):
        if round_idx > 0 and reload_url:
            try:
                print(f"进入第 {round_idx + 1}/{max_rounds} 轮，重开登录页: {reload_url}")
                page.goto(reload_url, wait_until="domcontentloaded", timeout=page_timeout)
                page.wait_for_timeout(random.randint(1200, 1800) if fast_mode else random.randint(2200, 3000))
            except Exception as e:
                print(f"重开登录页失败: {e}")
                continue

        for attempt in range(max_attempts):
            print(f"\n--- 验证码轮次 {round_idx + 1}/{max_rounds}，尝试 {attempt + 1}/{max_attempts} ---")
            if attempt > 0:
                time.sleep(random.uniform(0.1, 0.3) if fast_mode else random.uniform(0.5, 1.0))
            dismiss_global_modal(page)

            page_text = page.inner_text("body") if page.query_selector("body") else ""
            if any(sig.lower() in page_text.lower() for sig in rate_limit_signatures):
                print("[WARNING] 检测到验证码限流/异常签名")
                cooldown = min(MAX_COOLDOWN_SECONDS, max(0, random.uniform(cooldown_min_sec, cooldown_max_sec)))
                if cooldown > 0:
                    print(f"[WARNING] 冷却 {cooldown:.1f} 秒后重试")
                    time.sleep(cooldown)
                if round_idx == max_rounds - 1:
                    return "rate_limited"
                break

            captcha_type, captcha_element = detect_captcha_type(page)
            if captcha_type == "unknown":
                print("未检测到验证码，进行稳定性确认...")
                if _captcha_gone_stably(page):
                    print("验证码已稳定消失，判定通过")
                    return True
                print("验证码可能仍在或短暂重绘，继续尝试")
                continue

            if captcha_type == "click":
                prompt_element = page.query_selector("#tagLabel, .bcap-text-message-title2")
                prompt_text = prompt_element.inner_text() if prompt_element else "选择正确的图片"
                screenshot_bytes = captcha_element.screenshot()
                screenshot_base64 = screenshot_to_base64(screenshot_bytes)
                try:
                    result = analyze_click_captcha(api_key, primary_model, screenshot_base64, prompt_text)
                    positions = parse_json_response(result).get("positions", [])
                    if not positions:
                        print("[WARNING] 点击验证码未识别到有效位置，进入下一次尝试")
                        continue

                    clicked = click_captcha_images(page, positions, click_retry_per_cell=click_retry_per_cell)
                    if not clicked:
                        print("[WARNING] 点击验证码本轮未成功点击任何格子，进入下一次尝试")
                        continue

                    page.wait_for_timeout(random.randint(500, 800) if fast_mode else random.randint(800, 1200))

                    verify_clicked = False
                    verify_selectors = [
                        ".bcap-verify-button",
                        "button:has-text('验证')",
                        "button:has-text('确认')",
                        "button:has-text('提交')",
                        "button:has-text('Verify')",
                        "button:has-text('Confirm')",
                        "[class*='verify']",
                    ]
                    for selector in verify_selectors:
                        try:
                            dismiss_global_modal(page)
                            verify_btn = page.query_selector(selector)
                            if verify_btn and verify_btn.is_visible():
                                verify_btn.click()
                                verify_clicked = True
                                print(f"点击了验证码确认按钮: {selector}")
                                break
                        except Exception:
                            pass

                    if not verify_clicked:
                        try:
                            page.keyboard.press("Enter")
                            print("未找到确认按钮，尝试回车提交")
                        except Exception:
                            pass

                    page.wait_for_timeout(random.randint(600, 900) if fast_mode else random.randint(1000, 1500))
                    # 验证码稳定消失才判定通过
                    if _captcha_gone_stably(page):
                        return True

                    # 第二轮验证码：等待新验证码完全加载
                    print("[状态] 点击验证码仍存在，等待第二轮验证码加载...")
                    page.wait_for_timeout(1000)  # 先等待 1 秒让旧验证码消失
                    for _ in range(15):
                        container = page.query_selector(".bcap-modal, .bcapc-popup")
                        if container:
                            # 检查图片是否加载完成
                            img = container.query_selector(".bcap-image11")
                            if img and img.is_visible():
                                print("[状态] 第二轮验证码已加载")
                                break
                        page.wait_for_timeout(200)
                    page.wait_for_timeout(500)  # 额外等待确保稳定
                except Exception as e:
                    print(f"识别失败: {e}")

            if captcha_type == "slider":
                slider_bg = page.query_selector(".bs-main-image, [class*='slider-bg'], [class*='captcha-bg'], .bcap-bg, [class*='verify-img']")
                if slider_bg:
                    screenshot_bytes = slider_bg.screenshot()
                    box = slider_bg.bounding_box()
                else:
                    screenshot_bytes = captcha_element.screenshot()
                    box = captcha_element.bounding_box()

                image_width = int(box["width"]) if box else 300
                screenshot_base64 = screenshot_to_base64(screenshot_bytes)
                debug_prefix = f"slider_{email_addr.split('@')[0] if email_addr else 'unknown'}_r{round_idx+1}_{attempt+1}"

                try:
                    # 等待滑块完全加载
                    page.wait_for_timeout(500)

                    slider_btn = None
                    slider_selectors = [
                        ".bs-slide-thumb",
                        ".bcap-slider-btn",
                        "[class*='slider-button']",
                        "[class*='drag-btn']",
                        "[class*='slide-thumb']",
                        "[class*='slider-btn']",
                        ".slider-button",
                        ".drag-button",
                        "[class*='thumb']",
                        "div[class*='slide'] > div",
                        "div[class*='slider'] > div",
                    ]

                    for selector in slider_selectors:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            # 检查元素是否可交互
                            try:
                                box = btn.bounding_box()
                                if box and box["width"] > 0 and box["height"] > 0:
                                    slider_btn = btn
                                    print(f"[滑块] 找到滑块按钮: {selector}, 位置: ({box['x']:.1f}, {box['y']:.1f}), 尺寸: {box['width']:.1f}x{box['height']:.1f}")
                                    break
                            except:
                                continue

                    if not slider_btn:
                        print("[ERROR] 未找到滑块按钮")
                        print("[DEBUG] 尝试列出所有可能的滑块元素...")
                        all_candidates = page.query_selector_all("[class*='slide'], [class*='slider'], [class*='drag'], [class*='thumb']")
                        for i, el in enumerate(all_candidates[:5]):
                            try:
                                if el.is_visible():
                                    class_name = el.get_attribute("class") or ""
                                    tag = el.evaluate("el => el.tagName")
                                    box = el.bounding_box()
                                    if box:
                                        print(f"  候选[{i}]: <{tag}> class='{class_name[:50]}' size={box['width']:.0f}x{box['height']:.0f}")
                            except:
                                pass
                        continue

                    # ========== AI 识别 ==========
                    MAX_FIT_ATTEMPTS = 3
                    last_ai_distance = None
                    fit_success = False

                    for fit_attempt in range(MAX_FIT_ATTEMPTS):
                        print(f"\n[拟合] 第 {fit_attempt + 1}/{MAX_FIT_ATTEMPTS} 次尝试...")

                        # 每次拟合都重新截图
                        if fit_attempt > 0:
                            page.wait_for_timeout(random.randint(500, 800))
                            slider_bg_new = page.query_selector(".bs-main-image, [class*='slider-bg'], [class*='captcha-bg'], .bcap-bg, [class*='verify-img']")
                            if slider_bg_new:
                                screenshot_bytes = slider_bg_new.screenshot()
                                box = slider_bg_new.bounding_box()
                            else:
                                screenshot_bytes = captcha_element.screenshot()
                                box = captcha_element.bounding_box()
                            image_width = int(box["width"]) if box else 300
                            screenshot_base64 = screenshot_to_base64(screenshot_bytes)

                        # 保存原始背景图到 debug
                        try:
                            from .slider_cv import bytes_to_cv_image, ensure_debug_dir, DEBUG_DIR
                            import cv2 as _cv2
                            import numpy as _np

                            ensure_debug_dir()
                            bg_img = bytes_to_cv_image(screenshot_bytes)
                            if bg_img is not None:
                                _cv2.imwrite(str(DEBUG_DIR / f"{debug_prefix}_fit{fit_attempt+1}_bg.png"), bg_img)

                                # 打高精度刻度尺
                                h, w = bg_img.shape[:2]
                                ruler_h = 40
                                ruler_img = _np.ones((h + ruler_h, w, 3), dtype=_np.uint8) * 255
                                ruler_img[ruler_h:, :] = bg_img

                                for x in range(0, w + 1):
                                    if x % 50 == 0:
                                        _cv2.line(ruler_img, (x, 0), (x, ruler_h), (0, 0, 255), 2)
                                        _cv2.line(ruler_img, (x, ruler_h), (x, h + ruler_h), (0, 0, 255), 1)
                                        label = str(x)
                                        _cv2.putText(ruler_img, label, (x - len(label)*5, 15),
                                                    _cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
                                    elif x % 10 == 0:
                                        _cv2.line(ruler_img, (x, ruler_h - 15), (x, ruler_h), (255, 0, 0), 1)
                                        _cv2.putText(ruler_img, str(x), (x - 8, ruler_h - 2),
                                                    _cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 0, 0), 1)
                                    elif x % 5 == 0:
                                        _cv2.line(ruler_img, (x, ruler_h - 8), (x, ruler_h), (150, 150, 150), 1)

                                _cv2.rectangle(ruler_img, (0, ruler_h), (60, h + ruler_h), (0, 255, 0), 3)
                                _cv2.imwrite(str(DEBUG_DIR / f"{debug_prefix}_fit{fit_attempt+1}_ruler.png"), ruler_img)
                        except Exception:
                            pass

                        # AI 识别
                        print("[AI] 调用 AI 识别缺口位置...")
                        try:
                            ai_result = analyze_slider_captcha(api_key, primary_model, screenshot_base64, image_width)
                            ai_data = parse_json_response(ai_result)
                            ai_gap_x = int(ai_data.get("gap_x", 0))
                            ai_puzzle_x = int(ai_data.get("puzzle_x", 0))
                            ai_distance = int(ai_data.get("drag_distance", 0))
                            # gap_x 就是缺口左边缘，滑动距离 = gap_x - puzzle_x
                            last_ai_distance = ai_gap_x - ai_puzzle_x if ai_gap_x else ai_distance
                            if last_ai_distance <= 0 and ai_gap_x > 0:
                                last_ai_distance = ai_gap_x
                            print(f"[AI] 识别结果: gap_x={ai_gap_x}, puzzle_x={ai_puzzle_x}, distance={last_ai_distance}")

                            # 保存 AI 结果标注图
                            try:
                                if bg_img is not None:
                                    ai_img = ruler_img.copy()
                                    _cv2.line(ai_img, (ai_gap_x, 0), (ai_gap_x, h + ruler_h), (0, 255, 255), 2)
                                    _cv2.putText(ai_img, f"AI:{ai_gap_x}", (ai_gap_x + 3, ruler_h + 20),
                                                _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
                                    _cv2.imwrite(str(DEBUG_DIR / f"{debug_prefix}_fit{fit_attempt+1}_ai_result.png"), ai_img)
                            except Exception:
                                pass

                        except Exception as e:
                            print(f"[AI] 识别失败: {e}")
                            continue

                        if not ai_gap_x:
                            print("[AI] 未获取到有效的缺口位置")
                            continue

                        # 直接使用 AI 结果执行滑动
                        print(f"[AI] 准备执行滑动: {last_ai_distance}px (gap_x={ai_gap_x})")

                        # 确保没有遮挡
                        dismiss_global_modal(page)
                        page.wait_for_timeout(200)

                        # 再次确认滑块按钮仍然可见
                        if not slider_btn.is_visible():
                            print("[AI] 警告: 滑块按钮不可见，重新查找...")
                            slider_btn = None
                            for selector in slider_selectors:
                                btn = page.query_selector(selector)
                                if btn and btn.is_visible():
                                    slider_btn = btn
                                    break
                            if not slider_btn:
                                print("[AI] 错误: 无法重新找到滑块按钮")
                                break

                        # 执行滑动
                        drag_success = simulate_human_drag(page, slider_btn, last_ai_distance)
                        if not drag_success:
                            print("[AI] 滑动执行失败")
                            break

                        # 使用统一检测逻辑，避免选择器覆盖不全导致误判
                        if _captcha_gone_stably(page):
                            print("[AI] 滑块验证码通过!")
                            return True
                        else:
                            print("[AI] 滑动后验证码仍存在，继续下一次尝试...")
                            break
                    else:
                        # 达到最大尝试次数，使用最后的 AI 结果
                        if last_ai_distance:
                            print(f"[保底] 达到最大尝试次数，使用 AI 结果: {last_ai_distance}px")

                            # 确保没有遮挡
                            dismiss_global_modal(page)
                            page.wait_for_timeout(200)

                            # 再次确认滑块按钮
                            if not slider_btn.is_visible():
                                print("[保底] 警告: 滑块按钮不可见，重新查找...")
                                slider_btn = None
                                for selector in slider_selectors:
                                    btn = page.query_selector(selector)
                                    if btn and btn.is_visible():
                                        slider_btn = btn
                                        break
                                if not slider_btn:
                                    print("[保底] 错误: 无法重新找到滑块按钮")
                                    continue

                            # 执行滑动
                            drag_success = simulate_human_drag(page, slider_btn, last_ai_distance)
                            if not drag_success:
                                print("[保底] 滑动执行失败")
                                continue

                            if _captcha_gone_stably(page):
                                print("[保底] 滑块验证码通过!")
                                return True

                except Exception as e:
                    print(f"识别失败: {e}")

            page.wait_for_timeout(random.randint(500, 900) if fast_mode else random.randint(1000, 1500))

    print("验证码尝试次数已用完")
    return False


def solve_captcha_if_present(
    page,
    api_key,
    model,
    email_addr="",
    captcha_config=None,
    reload_url=None,
    page_timeout=60000,
):
    captcha_type, _ = detect_captcha_type(page)
    if captcha_type != "unknown":
        print(f"检测到{captcha_type}验证码，开始识别...")
        captcha_config = captcha_config or {}
        return solve_captcha(
            page,
            api_key,
            model,
            max_attempts=captcha_config.get("max_attempts_per_round", 5),
            email_addr=email_addr,
            retry_mode=captcha_config.get("retry_mode", "fast"),
            max_rounds=captcha_config.get("max_rounds", 3),
            reload_url=reload_url,
            page_timeout=page_timeout,
            cooldown_min_sec=captcha_config.get("cooldown_on_risk_min_sec", 20),
            cooldown_max_sec=captcha_config.get("cooldown_on_risk_max_sec", 60),
            click_retry_per_cell=captcha_config.get("click_retry_per_cell", 3),
            auto_tune_slider_offset=captcha_config.get("auto_tune_slider_offset", True),
        )
    return True
