# services/llm_service.py
import yaml
import json
import requests # 假设您通过 HTTP 调用本地部署的 vLLM / Ollama
from django.conf import settings

# 加载配置
YAML_PATH = settings.BASE_DIR / 'config' / 'llm_rules.yaml'

def load_yaml():
    with open(YAML_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def call_local_llm(prompt_text, max_tokens=150, temperature=0.7):
    """
    通用大模型调用底座 (需根据您实际的本地推理服务 API 调整)
    此处以常见的 OpenAI 兼容接口为例
    """
    url = "http://127.0.0.1:8080/v1/chat/completions" # 假设本地推理端口
    payload = {
        "model": "local-model",
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    try:
        # 本地开发可先用假数据 Mock 避免环境卡壳
        # return "做得很棒！但注意膝盖不要内扣，继续保持！" 
        response = requests.post(url, json=payload, timeout=10)
        return response.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"LLM Error: {e}")
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
        return json.loads(clean_text)
    except json.JSONDecodeError:
        return None