# services/llm_service.py
import yaml
import json
import re
import requests # 假设您通过 HTTP 调用本地部署的 vLLM / Ollama
from django.conf import settings


PLAN_JSON_SYSTEM_PROMPT = (
    "你是严格的JSON生成器。"
    "你只能输出一个合法JSON数组，不允许输出任何解释、注释、Markdown代码块。"
    "数组长度必须为7。"
    "每个元素必须且只能包含 day 与 exercises 两个键。"
    "day 为1-7整数，exercises为数组。"
)


class LocalLLMError(RuntimeError):
    """本地 LLM 服务调用失败或响应结构异常。"""

# 加载配置
YAML_PATH = settings.BASE_DIR / 'config' / 'llm_rules.yaml'

def load_yaml():
    with open(YAML_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def call_local_llm(prompt_text, max_tokens=150, temperature=0.7, system_prompt=None, raise_on_error=False):
    """
    通用大模型调用底座 (需根据您实际的本地推理服务 API 调整)
    此处以常见的 OpenAI 兼容接口为例
    """
    config = load_yaml() or {}
    api_client_cfg = config.get('api_client', {})
    models_cfg = config.get('models', {})

    base_url = str(api_client_cfg.get('base_url', 'http://127.0.0.1:8081')).rstrip('/')
    url = f"{base_url}/v1/chat/completions"
    model_name = str(models_cfg.get('default_local_model', 'qwen2.5-3b-rk3588'))
    timeout_sec = int(api_client_cfg.get('timeout_sec', 60))
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": str(system_prompt)})
    messages.append({"role": "user", "content": prompt_text})

    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        # 本地开发可先用假数据 Mock 避免环境卡壳
        # return "做得很棒！但注意膝盖不要内扣，继续保持！" 
        print(f"[LLM REQUEST] url={url} payload={json.dumps(payload, ensure_ascii=False)}")
        response = requests.post(url, json=payload, timeout=timeout_sec)
        print(f"[LLM RAW RESPONSE] status={response.status_code} body={response.text}")
        response.raise_for_status()
        data = response.json()
        content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
        if not isinstance(content, str) or not content.strip():
            raise LocalLLMError(f"LLM 响应缺少有效 content: {data}")
        return content.strip()
    except Exception as e:
        print(f"LLM Error: {e}")
        if raise_on_error:
            if isinstance(e, LocalLLMError):
                raise
            raise LocalLLMError(str(e)) from e
        return "加油，继续保持！" # 降级回复

def generate_micro_coaching(activity_type, error_text):
    config = load_yaml()
    prompt = config['prompts']['micro_coaching'].format(
        activity_type=activity_type,
        error_text=error_text
    )
    return call_local_llm(prompt, max_tokens=50, temperature=0.7)

def generate_post_workout_feedback(data_dict):
    config = load_yaml()
    prompt = config['prompts']['post_workout'].format(**data_dict)
    response_text = call_local_llm(prompt, max_tokens=500, temperature=0.3)
    try:
        # 清理 Markdown 代码块包裹
        clean_text = response_text.replace("```json", "").replace("```", "").strip()
        clean_text = re.sub(r"^\s*\[JSON\]\s*", "", clean_text, flags=re.IGNORECASE)
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            candidates = re.findall(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean_text)
            for candidate in candidates:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            return None
    except Exception:
        return None


def generate_post_workout_eval(data_dict):
    """仅生成训练评估分数与评语。"""
    config = load_yaml() or {}
    prompts = config.get('prompts', {})
    prompt_template = prompts.get('post_workout_eval') or prompts.get('post_workout')
    prompt = prompt_template.format(**data_dict)
    response_text = call_local_llm(prompt, max_tokens=220, temperature=0.2)

    try:
        clean_text = response_text.replace("```json", "").replace("```", "").strip()
        clean_text = re.sub(r"^\s*\[JSON\]\s*", "", clean_text, flags=re.IGNORECASE)
        try:
            parsed = json.loads(clean_text)
        except json.JSONDecodeError:
            candidates = re.findall(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean_text)
            parsed = None
            for candidate in candidates:
                try:
                    parsed = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
        if not isinstance(parsed, dict):
            return None

        return {
            "quality_score": parsed.get("quality_score", 5),
            "feedback_text": parsed.get("feedback_text", "干得很棒！"),
        }
    except Exception:
        return None


def generate_post_workout_new_plan(data_dict):
    """仅生成下周训练计划（7天JSON数组）。"""
    config = load_yaml() or {}
    prompts = config.get('prompts', {})
    prompt_template = prompts.get('post_workout_new_plan')
    if not prompt_template:
        return None

    prompt = prompt_template.format(**data_dict)
    response_text = call_local_llm(
        prompt,
        max_tokens=700,
        temperature=0.0,
        system_prompt=PLAN_JSON_SYSTEM_PROMPT,
    )

    try:
        clean_text = response_text.replace("```json", "").replace("```", "").strip()
        clean_text = re.sub(r"^\s*\[JSON\]\s*", "", clean_text, flags=re.IGNORECASE)
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            candidates = re.findall(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean_text)
            for candidate in candidates:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            return None
    except Exception:
        return None