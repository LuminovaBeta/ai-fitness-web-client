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
    config = load_yaml() or {}
    api_client_cfg = config.get('api_client', {})
    models_cfg = config.get('models', {})

    base_url = str(api_client_cfg.get('base_url', 'http://127.0.0.1:8081')).rstrip('/')
    url = f"{base_url}/v1/chat/completions"
    model_name = str(models_cfg.get('default_local_model', 'qwen2.5-3b-rk3588'))
    timeout_sec = int(api_client_cfg.get('timeout_sec', 60))
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt_text}],
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