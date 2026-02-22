import base64
import json
import requests


def screenshot_to_base64(screenshot_bytes: bytes) -> str:
    return base64.standard_b64encode(screenshot_bytes).decode("utf-8")


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


def analyze_slider_captcha(api_key, model, screenshot_base64, image_width):
    """AI 识别滑块验证码"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

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


def parse_json_response(result):
    clean_result = result.strip()
    if clean_result.startswith("```"):
        lines = clean_result.split("\n")
        clean_result = "\n".join(lines[1:-1])
    return json.loads(clean_result)
