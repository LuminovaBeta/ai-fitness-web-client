# services/tts_service.py
import edge_tts
import asyncio
import os
import platform
import tempfile
import subprocess
import threading
import yaml

from django.conf import settings

_PLAYER_LOCK = threading.Lock()
_CURRENT_PLAYER_PROCESS = None
_DEFAULT_TTS_VOICE = None


def _get_default_tts_voice():
    """从 llm_rules.yaml 读取默认 TTS 音色，读取失败时使用兜底值。"""
    global _DEFAULT_TTS_VOICE
    if _DEFAULT_TTS_VOICE:
        return _DEFAULT_TTS_VOICE

    fallback = "zh-CN-YunjianNeural"
    try:
        yaml_path = settings.BASE_DIR / "config" / "llm_rules.yaml"
        with open(yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        voice = str((cfg.get("models", {}) or {}).get("tts_voice", "")).strip()
        _DEFAULT_TTS_VOICE = voice or fallback
    except Exception:
        _DEFAULT_TTS_VOICE = fallback
    return _DEFAULT_TTS_VOICE

async def _generate_audio(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice, rate="+10%")
    await communicate.save(output_path)


def stop_tts_playback():
    """停止当前正在进行的本地音频播放（最佳努力）。"""
    global _CURRENT_PLAYER_PROCESS

    with _PLAYER_LOCK:
        proc = _CURRENT_PLAYER_PROCESS
        _CURRENT_PLAYER_PROCESS = None

    if not proc:
        return False

    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except Exception:
                proc.kill()
        return True
    except Exception as e:
        print(f"停止 TTS 播放失败: {e}")
        return False


def _start_audio_process(output_path, audio_duration: float):
    """启动可中断的本地播放进程，并保存句柄。"""
    global _CURRENT_PLAYER_PROCESS

    stop_tts_playback()

    proc = None
    if platform.system() == "Windows":
        # 通过独立 powershell 进程播放 mp3，便于后续 terminate 强制中断
        # 注意：使用 file URI 避免路径空格导致的解析问题
        normalized_path = output_path.replace('\\', '/')
        file_uri = f"file:///{normalized_path}"
        safe_duration = max(1.0, float(audio_duration) + 1.0)
        ps_script = (
            "Add-Type -AssemblyName presentationCore; "
            "$player = New-Object System.Windows.Media.MediaPlayer; "
            f"$player.Open([Uri]'{file_uri}'); "
            "$player.Play(); "
            f"Start-Sleep -Milliseconds {int(safe_duration * 1000)}; "
            "$player.Close();"
        )
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(
            ["mpg123", "-q", output_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    with _PLAYER_LOCK:
        _CURRENT_PLAYER_PROCESS = proc

def play_tts_sync(text, voice=None):
    if not text:
        return 0 # 返回 0 秒

    if not voice:
        voice = _get_default_tts_voice()
    
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, "coach.mp3")
    audio_duration = 0
        
    try:
        asyncio.run(_generate_audio(text, voice, output_path))
        
        if os.path.exists(output_path):
            # ====== 新增：解析 MP3 时长 ======
            try:
                from mutagen.mp3 import MP3
                audio = MP3(output_path)
                audio_duration = audio.info.length # 单位：秒 (float)
            except Exception as e:
                # 降级方案：如果没装 mutagen 或解析失败，按文字长度粗略估算 (约 4个字/秒)
                print(f"解析音频时长失败，使用估算: {e}")
                audio_duration = len(text) / 4.0 
            # ==================================

            print(f"[TTS] 语音生成成功，内容：'{text}' (时长: {audio_duration:.2f}s)")
            _start_audio_process(output_path, audio_duration)
                
    except Exception as e:
        print(f"TTS 播放失败: {e}")
        
    return audio_duration # 返回播放时长