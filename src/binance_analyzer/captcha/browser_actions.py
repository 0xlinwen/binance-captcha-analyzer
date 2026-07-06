"""验证码页面动作。"""

from __future__ import annotations

import random
import time

from ..utils import dismiss_global_modal


def _normalize_captcha_positions(positions) -> list[tuple[int, int]]:
    """把 AI 返回的网格坐标转换为页面 DOM 使用的 1-3 行列坐标。"""
    normalized_positions: list[tuple[int, int]] = []
    parsed_positions: list[tuple[int, int]] = []
    for position in positions or []:
        try:
            row, col = position
            parsed_positions.append((int(row), int(col)))
        except (TypeError, ValueError):
            continue

    is_zero_based = any(row == 0 or col == 0 for row, col in parsed_positions)
    for row, col in parsed_positions:
        dom_row = row + 1 if is_zero_based else row
        dom_col = col + 1 if is_zero_based else col
        if 1 <= dom_row <= 3 and 1 <= dom_col <= 3:
            normalized_positions.append((dom_row, dom_col))
    return normalized_positions


def simulate_human_drag(page, slider_element, distance: int) -> bool:
    """模拟人类滑动行为。"""
    try:
        box = slider_element.bounding_box()
        if not box:
            print("[滑动] 错误: 无法获取滑块位置")
            return False

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        end_x = start_x + distance

        print(f"[滑动] 起点: ({start_x:.1f}, {start_y:.1f}), 终点: ({end_x:.1f}, {start_y:.1f}), 距离: {distance}px")
        page.mouse.move(start_x, start_y)
        time.sleep(random.uniform(0.1, 0.2))
        page.mouse.down()
        time.sleep(random.uniform(0.05, 0.1))

        steps = random.randint(20, 30)
        easing_type = random.choice(["ease_out", "ease_in_out", "linear_with_pause"])
        pause_at = random.uniform(0.3, 0.4) if easing_type == "linear_with_pause" else None

        for index in range(steps):
            progress = (index + 1) / steps
            if easing_type == "ease_out":
                eased = progress * (2 - progress)
            elif easing_type == "ease_in_out":
                eased = progress * progress * (3 - 2 * progress)
            elif pause_at and abs(progress - pause_at) < 0.05:
                eased = pause_at
            else:
                eased = progress

            target_x = start_x + distance * eased
            jitter_y = random.uniform(-2.0, 2.0)
            page.mouse.move(target_x, start_y + jitter_y)
            time.sleep(random.uniform(0.01, 0.03))

        page.mouse.move(start_x + distance, start_y)
        time.sleep(random.uniform(0.1, 0.15))
        page.mouse.up()
        time.sleep(random.uniform(1.0, 3.0))
        return True
    except Exception as exc:
        print(f"[滑动] 异常: {exc}")
        return False


def click_at_page_coordinate(page, x: float, y: float) -> None:
    """在页面绝对坐标 (x, y) 处模拟人类点击（CSS 像素）。

    坐标应为页面视口坐标系，调用方需先把 AI 返回的截图相对坐标
    换算为页面绝对坐标（加容器偏移、按缩放比还原）后再传入。
    """
    jitter_x = x + random.uniform(-2.0, 2.0)
    jitter_y = y + random.uniform(-2.0, 2.0)
    page.mouse.move(jitter_x, jitter_y)
    time.sleep(random.uniform(0.1, 0.3))
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.12))
    page.mouse.up()
    time.sleep(random.uniform(0.2, 0.4))


def click_captcha_images(page, positions, click_retry_per_cell: int = 3):
    """点击当前可见点击验证码容器中的图片格子。"""
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

    for row, col in _normalize_captcha_positions(positions):
        full_selector = f".bcap-modal .bcap-image{row}{col}, .bcapc-popup .bcap-image{row}{col}"
        success = False
        last_err = "element_not_clickable"

        for _attempt in range(max(1, click_retry_per_cell)):
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
                    center_x = box["x"] + box["width"] / 2
                    center_y = box["y"] + box["height"] / 2
                    if (
                        container_box["x"] <= center_x <= container_box["x"] + container_box["width"]
                        and container_box["y"] <= center_y <= container_box["y"] + container_box["height"]
                    ):
                        valid_element = elem
                        break

                if not valid_element:
                    time.sleep(0.2)
                    continue

                box = valid_element.bounding_box()
                if box:
                    target_x = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
                    target_y = box["y"] + box["height"] / 2 + random.uniform(-5, 5)
                    page.mouse.move(target_x, target_y)
                    time.sleep(random.uniform(0.1, 0.3))

                valid_element.click(timeout=3000)
                clicked.append((row, col))
                print(f"  点击了位置 ({row},{col})")
                time.sleep(random.uniform(0.25, 0.45))
                success = True
                break
            except Exception as exc:
                last_err = str(exc)[:50]
                time.sleep(random.uniform(0.2, 0.35))

        if not success:
            print(f"  点击位置 ({row},{col}) 失败: {last_err}")

    return clicked
