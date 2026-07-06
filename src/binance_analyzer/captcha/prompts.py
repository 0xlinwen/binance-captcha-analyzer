"""验证码 AI 提示词模板。

新增验证码类型时，把提示词放在这里，solver 只负责截图、调用和页面动作。
"""

from __future__ import annotations


CLICK_CAPTCHA_PROMPT = '''这是一个验证码图片，是一个 3x3 的图片网格。
提示文字是："{prompt_text}"

请分析这个验证码，告诉我应该点击哪些图片。
图片位置用行列表示，从左上角开始：
- 第1行第1列 = (1,1), 第1行第2列 = (1,2), 第1行第3列 = (1,3)
- 第2行第1列 = (2,1), 第2行第2列 = (2,2), 第2行第3列 = (2,3)
- 第3行第1列 = (3,1), 第3行第2列 = (3,2), 第3行第3列 = (3,3)

请只返回 JSON 格式，例如：
{{"positions": [[1,2], [2,3], [3,1]]}}

不要返回其他内容，只返回 JSON。'''


SLIDER_CAPTCHA_PROMPT = '''分析这个滑块验证码图片。

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
