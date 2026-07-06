"""验证码 AI 提示词模板。

新增验证码类型时，把提示词放在这里，solver 只负责截图、调用和页面动作。
"""

from __future__ import annotations


CLICK_CAPTCHA_PROMPT = """这是一个验证码图片，是一个 3x3 的图片网格。
提示文字是："{prompt_text}"

请识别出哪些图片符合提示文字的要求，并返回这些图片的位置。

图片位置说明：
- 行号（row）：从上到下，0、1、2
- 列号（col）：从左到右，0、1、2

例如：
- 左上角：[0, 0]
- 中间：[1, 1]
- 右下角：[2, 2]

返回格式（JSON）：
{{
  "positions": [[row1, col1], [row2, col2], ...]
}}

注意事项：
1. 仔细观察每个图片，确保符合提示文字的要求
2. 如果没有符合的图片，返回空数组：{{"positions": []}}
3. 只返回 JSON 格式，不要包含其他文字
4. 位置坐标必须是 0-2 之间的整数

请开始识别：
"""


SLIDER_CAPTCHA_PROMPT = """这是一个滑块验证码图片。

请识别出图片中缺口的位置（X 坐标）。

返回格式（JSON）：
{{
  "gap_x": <缺口的 X 坐标，整数>
}}

注意事项：
1. 仔细观察图片，找到明显的缺口或拼图块位置
2. X 坐标是从左到右的像素位置
3. 只返回 JSON 格式，不要包含其他文字
4. 坐标必须是正整数

请开始识别：
"""


CHECKBOX_CAPTCHA_PROMPT = '''这是一张 Binance/BCaptcha 安全验证弹窗截图，截图尺寸为 {image_width}x{image_height} 像素。
坐标原点 (0,0) 在截图左上角，x 向右增大，y 向下增大。

弹窗中有一个"进行人机身份验证"的复选框。它通常是提示文字左侧的黄色或浅色方形按钮，可能已经显示黑色对勾。
任务：找到这个复选框方块本体的中心点，返回应该点击的像素坐标。

要求：
- 必须返回左侧方形按钮/复选框本体的正中心坐标
- 不要返回"进行人机身份验证"文字区域
- 不要返回弹窗空白区域、关闭按钮、Logo 或其它装饰区域
- 如果截图里有红框标注，红框通常只是提示位置，仍然要返回红框内方形按钮本体的中心点
- 坐标必须落在截图范围内（0 <= x <= {image_width}，0 <= y <= {image_height}）
- 如果截图中找不到可点击的复选框，返回 {{"found": false}}

只返回 JSON 格式，例如：
{{"found": true, "x": 123, "y": 456}}

不要返回其他内容，只返回 JSON。'''


def build_click_captcha_prompt(prompt_text: str) -> str:
    """生成点击验证码提示词。"""
    return CLICK_CAPTCHA_PROMPT.format(prompt_text=prompt_text)


def build_checkbox_captcha_prompt(image_width: int, image_height: int) -> str:
    """生成勾选复选框（人机身份验证）提示词。

    用于识别 "进行人机身份验证" 复选框的中心点像素坐标，
    坐标相对于传入的截图左上角。
    """
    return CHECKBOX_CAPTCHA_PROMPT.format(image_width=image_width, image_height=image_height)


def build_slider_captcha_prompt(image_width: int) -> str:
    """生成滑块验证码提示词。"""
    return SLIDER_CAPTCHA_PROMPT.format(image_width=image_width)
