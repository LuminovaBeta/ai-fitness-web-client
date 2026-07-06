# services/llm_service.py
import yaml
import json
from django.conf import settings

# 加载配置
YAML_PATH = settings.BASE_DIR / 'config' / 'llm_rules.yaml'

def load_yaml():
    with open(YAML_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def call_local_llm(prompt_text, max_tokens=150, temperature=0.7):
    """
    [Windows Mock 版] 不发网络请求，直接返回假结果
    """
    print(f"\n--- [LLM Mock 被调用] ---")
    
    # 1. 拦截“初始化计划生成” (寻找 Prompt 中的关键字，如"纯JSON数组" 或 "7天的训练计划")
    if "纯JSON数组" in prompt_text or "```json" in prompt_text or "JSON" in prompt_text:
        print("-> 触发 [初始化计划] Mock")
        return """
        ```json
        [
          {
            "day": 1,
            "exercises": [
              {"type": "squat", "sets": 3, "reps_per_set": 15, "rest_sec": 60},
              {"type": "push_up", "sets": 3, "reps_per_set": 10, "rest_sec": 45}
            ]
          }
        ]
        ```
        """
    
    # 2. 拦截“私教问答 Chatbot”
    print("-> 触发 [聊天/微指导] Mock")
    return "您的数据看起来非常健康！继续保持每天锻炼的好习惯哦。"

def generate_micro_coaching(activity_type, error_text):
    return "假装这是一句纠正：注意核心收紧，稳住重心！"

def generate_post_workout_feedback(data_dict):
    """
    [Windows Mock 版] 固定运动后的后台 JSON 分析报告
    """
    print(f"\n--- [LLM Mock 运动结算后台分析开启] --- \n入参: {data_dict}")
    # 返回符合要求（包含 quality_score, feedback_text, new_plan）的合法字典
    return {
        "quality_score": 9,
        "feedback_text": "这是一段 Mock 评语：今天完成得很棒！心跳和血氧控制得非常好。",
        "new_plan": [
            {
            "day": 1,
            "exercises": [
                {"type": "squat", "sets": 4, "reps_per_set": 20, "rest_interval_sec": 45}
            ]
            }
        ]
    }