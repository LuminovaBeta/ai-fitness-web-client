from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _default_ros_config() -> dict[str, Any]:
    return {
        "runtime_mode": "windows_debug",
        "session_realtime": {
            "source_mode": "simulated",
            "relay_ttl_sec": 20,
        },
        "profiles": {
            "windows_debug": {
                "rosbridge_url": "ws://127.0.0.1:9090",
                "mjpeg_stream_url": "http://127.0.0.1:8080/stream?topic=/pose/image&type=mjpeg",
                "reconnect_delay_ms": 3000,
            }
        },
        "topics": {
            "pose_image": "/pose/image",
            "squat": {
                "control": "/squat/control",
                "state": "/squat/state",
                "rep_completed": "/squat/rep_completed",
                "errors": "/squat/errors",
            },
            "heart": {
                "control": "/heart_sensor_node/control",
                "heart_rate": "/heart_sensor_node/heart_rate",
                "spo2": "/heart_sensor_node/spo2",
                "packet_loss": "/heart_sensor_node/packet_loss",
            },
            "llm": {
                "output_string": "/rkllm/output_string",
            },
        },
        "action_detectors": [
            {
                "code": "squat",
                "name_zh": "深蹲",
                "name_en": "Squat",
                "ros_package": "squat_evaluator",
                "enabled": True,
                "topics": {
                    "control": "/squat/control",
                    "state": "/squat/state",
                    "rep_completed": "/squat/rep_completed",
                    "errors": "/squat/errors",
                },
            }
        ],
    }


def load_ros_runtime_config(base_dir: Path) -> dict[str, Any]:
    config_path = base_dir / "config" / "ros_runtime.yaml"
    config = _default_ros_config()

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                config.update(loaded)

    runtime_mode = config.get("runtime_mode") or "windows_debug"
    profiles = config.get("profiles") or {}
    active_profile = profiles.get(runtime_mode) or profiles.get("windows_debug") or {}

    config["active_profile"] = active_profile
    config["debug_mode"] = runtime_mode == "windows_debug"

    raw_detectors = config.get("action_detectors") or []
    enabled_detectors = []
    if isinstance(raw_detectors, list):
        for item in raw_detectors:
            if not isinstance(item, dict):
                continue
            if not item.get("enabled", True):
                continue
            code = str(item.get("code", "")).strip()
            if not code:
                continue
            enabled_detectors.append(item)

    config["enabled_action_detectors"] = enabled_detectors
    config["exercise_dictionary"] = [
        {
            "code": item.get("code"),
            "name": item.get("name_zh") or item.get("name_en") or item.get("code"),
            "name_zh": item.get("name_zh") or item.get("code"),
            "name_en": item.get("name_en") or item.get("code"),
            "ros_package": item.get("ros_package", ""),
        }
        for item in enabled_detectors
    ]
    return config
