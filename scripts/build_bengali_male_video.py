import asyncio
import json
import os
import re
import subprocess
import edge_tts
from gtts import gTTS
import requests

ROW_NUMBER = os.environ.get("ROW_NUMBER", "2")
SCRIPT_TEXT = os.environ.get("SCRIPT_TEXT", "").strip()
VISUAL_CUES = os.environ.get("VISUAL_CUES", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

ALLOWED_MALE_VOICES = [
    "bn-BD-PradeepNeural",
    "bn-IN-BashkarNeural",
]


def resolve_male_voice(requested_voice: str) -> list:
    req = (requested_voice or "").strip()
    ordered = []
    if req in ALLOWED_MALE_VOICES:
        ordered.append(req)
    for v in ALLOWED_MALE_VOICES:
        if v not in ordered:
            ordered.append(v)
    return ordered


def format_rate(raw_rate: str) -> str:
    cleaned = (raw_rate or "-4%").strip()
    if not cleaned.endswith("%"):
        cleaned = f"{cleaned}%"
    if not cleaned.startswith(("+", "-")):
        cleaned = f"+{cleaned}"
    return cleaned


async def _synth_edge_tts(text: str, out_mp3: str, voice: str, rate: str):
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(out_mp3)


def generate_bengali_male_voiceover(script_text: str, out_mp3: str = "voiceover.mp3") -> str:
    clean_text = re.sub(r"\s+", " ", script_text).strip()
    if not clean_text:
        clean_text = "পাহাড়ের শান্ত সকাল আর প্রকৃতির মাঝে হারিয়ে যাওয়ার এক ছোট্ট গল্প।"

    requested_voice = os.environ.get("TTS_VOICE", "bn-BD-PradeepNeural")
    rate = format_rate(os.environ.get("TTS_RATE", "-4%"))

    for voice in resolve_male_voice(requested_voice):
        try:
            asyncio.run(_synth_edge_tts(clean_text, out_mp3, voice=voice, rate=rate))
            if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 0:
                print(f"Generated Bengali male voiceover using {voice} at rate {rate}")
                return out_mp3
        except Exception as exc:
            print(f"Voice {voice} failed: {exc}")

    print("Falling back to gTTS Bengali ('bn')")
    gTTS(text=clean_text, lang="bn", slow=False).save(out_mp3)
    return out_mp3


def get_media_duration(file_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        file_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(json.loads(res.stdout)["format"]["duration"])


def parse_cues(raw_cues: str):
    scenes = []
    for line in raw_cues.splitlines():
        line = line.strip()
        if not line:
            continue

        time_match = re.search(r"\[(\d+)\s*-\s*(\d+)\s*s\]", line)
        if not time_match:
            continue
        start_s = int(time_match.group(1))
        end_s = int(time_match.group(2))
        duration = max(2.0, float(end_s - start_s))

        cc_match = re.search(r"Bengali\s*CC\s*:\s*(.+)$", line, re.IGNORECASE)
        caption = cc_match.group(1).strip() if cc_match else ""

        visual_part = re.sub(r"^Scene\s*\d+\s*\[\d+\s*-\s*\d+\s*s\]\s*:\s*", "", line, flags=re.IGNORECASE)
        visual_part = re.sub(r"\|?\s*Bengali\s*CC\s*:.*$", "", visual_part, flags=re.IGNORECASE).strip()

        query = re.sub(r"[^a-zA-Z\s]", " ", visual_part)
        query = " ".join(query.split()[:5]).strip() or "misty pine forest nature"

        scenes.append({
            "duration": duration,
            "query": query,
            "caption": caption,
        })

    if not scenes:
        scenes = [
            {"duration": 10.0, "query": "misty mountain forest morning", "caption": "শান্ত পাহাড়ি সকাল"},
            {"duration": 10.0, "query": "tea cup morning mist", "caption": "গরম চা আর প্রকৃতি"},
        ]
    return scenes


def fit_scenes_to_voice_duration(scenes: list, voice_duration: float) -> list:
    total_cue_dur = sum(s["duration"] for s in scenes) or 1.0
    target_total = max(5.0, voice_duration + 1.2)
    scale = target_total / total_cue_dur

    adjusted = []
    for s in scenes:
        item = dict(s)
        item["duration"] = max(2.0, round(s["duration"] * scale, 2))
        adjusted.append(item)
    return adjusted


def fetch_pexels_video(query: str, out_path: str) -> bool:
    if not PEXELS_API_KEY:
        return False
    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": 5, "orientation": "portrait"},
            timeout=20,
        )
        videos = resp.json().get("videos", [])
        if not videos:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": PEXELS_API_KEY},
                params={"query": query, "per_page": 5},
                timeout=20,
            )
            videos = resp.json().get("videos", [])

        for vid in videos:
            mp4s = [f for f in vid.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
            if mp4s:
                mp4s.sort(key=lambda x: abs((x.get("width") or 1080) - 1080))
                clip_bytes = requests.get(mp4s[0]["link"], timeout=40).content
                with open(out_path, "wb") as fh:
                    fh.write(clip_bytes)
                return True
    except Exception as exc:
        print(f"Pexels fallback for '{query}': {exc}")
    return False


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def find_bengali_font() -> str:
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansBengali-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    out = subprocess.check_output(["fc-list", ":lang=bn", "file"], text=True).splitlines()
    for line in out:
        font_file = line.split(":")[0].strip()
        if os.path.exists(font_file):
            return font_file
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def render_silent_scene(idx: int, scene: dict, font_path: str) -> str:
    dur = scene["duration"]
    raw_clip = f"raw_{idx}.mp4"
    seg_video = f"seg_{idx}.mp4"

    has_clip = fetch_pexels_video(scene["query"], raw_clip)

    vf_parts = []
    if has_clip:
        input_args = ["-stream_loop", "-1", "-i", raw_clip, "-t", str(dur)]
        vf_parts.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1")
    else:
        input_args = ["-f", "lavfi", "-i", f"color=c=0x132620:s=1080x1920:d={dur}:r=30"]
        vf_parts.append("setsar=1")

    if scene["caption"]:
        safe_cc = escape_drawtext(scene["caption"])
        vf_parts.append(
            f"drawtext=fontfile='{font_path}':text='{safe_cc}':"
            f"fontcolor=white:fontsize=52:box=1:boxcolor=black@0.55:boxborderw=18:"
            f"x=(w-text_w)/2:y=h*0.76"
        )

    cmd = [
        "ffmpeg",
        "-y",
        *input_args,
        "-vf",
        ",".join(vf_parts),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        seg_video,
    ]
    subprocess.run(cmd, check=True)
    return seg_video


def mix_voiceover_and_video(silent_video_path: str, voice_mp3: str, total_dur: float, out_mp4: str):
    filter_complex = (
        "[1:a]volume=1.15[vo];"
        f"[2:a]volume=0.04,atrim=0:{total_dur},asetpts=PTS-STARTPTS[amb];"
        "[vo][amb]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        silent_video_path,
        "-i",
        voice_mp3,
        "-f",
        "lavfi",
        "-t",
        str(total_dur),
        "-i",
        "anoisesrc=color=pink:r=44100:c=stereo",
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        str(total_dur),
        out_mp4,
    ]
    subprocess.run(cmd, check=True)


def main():
    voice_path = generate_bengali_male_voiceover(SCRIPT_TEXT, "voiceover.mp3")
    voice_dur = get_media_duration(voice_path)

    raw_scenes = parse_cues(VISUAL_CUES)
    scenes = fit_scenes_to_voice_duration(raw_scenes, voice_dur)
    font_path = find_bengali_font()

    segments = [render_silent_scene(i, s, font_path) for i, s in enumerate(scenes)]
    with open("concat.txt", "w", encoding="utf-8") as fh:
        for seg in segments:
            fh.write(f"file '{seg}'\n")

    silent_merged = "silent_merged.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            "concat.txt",
            "-c",
            "copy",
            silent_merged,
        ],
        check=True,
    )

    total_dur = sum(s["duration"] for s in scenes)
    mix_voiceover_and_video(silent_merged, voice_path, total_dur, "out.mp4")
    print(f"Successfully rendered Bengali male voiceover video out.mp4 ({total_dur:.1f}s) for row {ROW_NUMBER}.")


if __name__ == "__main__":
    main()
