from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import yt_dlp
import json
import os
import tempfile
import requests

app = FastAPI(title="YouTube Subtitle Fetcher API")

# Global token cache
_translator_token_cache = None

# Add CORS middleware to allow requests from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

@app.get("/subtitles")
def get_subtitles(video_id: str, lang: str = "ko"):
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    import requests
    
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'check_formats': False,
        'allow_unplayable_formats': True, # Cho phép lấy metadata kể cả khi không có format chạy được
        'ignore_no_formats_error': True,   # Không báo lỗi nếu không tìm thấy video format
    }

    # Kiểm tra sự hiện diện của cookies.txt
    cookie_path = "cookies.txt"
    if os.path.exists(cookie_path):
        print(f"✅ Found cookies.txt (Size: {os.path.getsize(cookie_path)} bytes)")
        ydl_opts['cookiefile'] = cookie_path
    else:
        print("⚠️  cookies.txt NOT found in /app directory")
            
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            # Step 1: Extract basic info (Metadata only)
            print(f"🔍 Extracting info for: {video_url}")
            try:
                info = ydl.extract_info(video_url, download=False)
            except Exception as ydl_err:
                print(f"❌ yt-dlp Error: {ydl_err}")
                raise HTTPException(status_code=500, detail=f"YouTube extraction error: {str(ydl_err)}")
            
            if not info:
                raise HTTPException(status_code=500, detail="Could not extract video metadata. Check if the video is private or blocked.")
            
            # Step 2: Find the subtitle URL in metadata
            subtitle_url = None
            found_format = None
            
            # Helper to find format in a track
            def find_best_url(formats):
                # Priority: json3 > srv3 > srv2 > srv1 > vtt
                preferred = ['json3', 'srv3', 'srv2', 'srv1', 'vtt']
                for p_ext in preferred:
                    for f in formats:
                        if f.get('ext') == p_ext:
                            return f['url'], p_ext
                return formats[0]['url'], formats[0].get('ext', 'unknown')

            # Look in manual subtitles first
            subtitles = info.get('subtitles', {})
            if lang in subtitles:
                subtitle_url, found_format = find_best_url(subtitles[lang])
            
            # If not found, look in automatic captions
            if not subtitle_url:
                auto = info.get('automatic_captions', {})
                if lang in auto:
                    subtitle_url, found_format = find_best_url(auto[lang])

            if not subtitle_url:
                # If still not found, check for generic language matches (e.g., 'en.*')
                for code, tracks in info.get('automatic_captions', {}).items():
                    if code.startswith(lang):
                        subtitle_url, found_format = find_best_url(tracks)
                        lang = code
                        break

            if not subtitle_url:
                raise HTTPException(status_code=404, detail=f"No subtitles found for video '{video_id}' in language '{lang}'")

            # Step 3: Fetch the content using requests (bypasses yt-dlp internal downloader)
            print(f"🔗 Using Subtitle URL ({found_format}): {subtitle_url[:100]}...")
            
            # Use same headers as yt-dlp if possible, or simple ones
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
            }
            
            resp = requests.get(subtitle_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"Failed to fetch subtitle content from YouTube: {resp.text[:200]}")

            # Step 4: Parse if it's JSON, otherwise return raw
            if found_format == 'json3' or '"events":' in resp.text:
                try:
                    data = resp.json()
                    parsed_subtitles = []
                    for event in data.get('events', []):
                        if 'segs' in event:
                            text = "".join([s.get('utf8', '') for s in event['segs']]).strip()
                            if text:
                                parsed_subtitles.append({
                                    'start': event.get('tStartMs', 0),
                                    'duration': event.get('dDurationMs', 0),
                                    'text': text
                                })

                    # Merge subtitle segments into complete sentences for better readability
                    subtitle_objects = [Subtitle(start=s['start'], duration=s['duration'], text=s['text']) for s in parsed_subtitles]
                    merged_subs, _ = merge_subtitle_segments(subtitle_objects)

                    # Convert back to dict format
                    merged_subtitles = [
                        {'start': s.start, 'duration': s.duration, 'text': s.text}
                        for s in merged_subs
                    ]

                    print(f"📦 Merged {len(parsed_subtitles)} segments into {len(merged_subtitles)} sentences")

                    return {
                        "success": True,
                        "video_id": video_id,
                        "language": lang,
                        "format": "json3",
                        "count": len(merged_subtitles),
                        "subtitles": merged_subtitles
                    }
                except:
                    # Fallback to raw if JSON parsing fails
                    pass

            return {
                "success": True,
                "video_id": video_id,
                "language": lang,
                "format": found_format,
                "raw_content": resp.text
            }
                
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=str(e))

def get_microsoft_translator_token():
    """Get free translation token from Microsoft Edge Translator"""
    global _translator_token_cache

    # Return cached token if available
    if _translator_token_cache:
        return _translator_token_cache

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Origin': 'https://www.youtube.com',
            'Referer': 'https://www.youtube.com/',
        }

        response = requests.get('https://edge.microsoft.com/translate/auth', headers=headers, timeout=10)

        if response.status_code == 200:
            token = response.text.strip()
            _translator_token_cache = token
            print(f"✅ Got Microsoft Translator token: {token[:50]}...")
            return token
        else:
            print(f"❌ Failed to get token: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting translator token: {e}")
        return None

class Subtitle(BaseModel):
    start: int
    duration: int
    text: str

class TranslateSubtitlesRequest(BaseModel):
    subtitles: List[Subtitle]
    to_lang: str = "vi"
    from_lang: str = ""

class TranslateRequest(BaseModel):
    texts: List[str]
    to_lang: str = "vi"
    from_lang: str = ""

def merge_subtitle_segments(subtitles: List[Subtitle], max_gap_ms: int = 1000, max_duration_ms: int = 8000):
    """
    Merge subtitle segments into complete sentences for better translation.

    Args:
        subtitles: List of subtitle segments
        max_gap_ms: Maximum gap between segments to merge (default 1000ms)
        max_duration_ms: Maximum duration of merged segment (default 8000ms)

    Returns:
        Tuple of (merged_subtitles, segment_mapping)
        - merged_subtitles: List of merged subtitle segments
        - segment_mapping: List mapping each original subtitle to its merged segment index
    """
    if not subtitles:
        return [], []

    merged = []
    segment_mapping = []
    current_group = []
    current_start = subtitles[0].start
    current_text = ""

    # Sentence ending punctuation
    sentence_endings = {'.', '!', '?', '。', '！', '？', '…'}

    for i, sub in enumerate(subtitles):
        # Check if we should start a new group
        should_merge = True

        if current_group:
            last_sub = current_group[-1]
            gap = sub.start - (last_sub.start + last_sub.duration)
            total_duration = (sub.start + sub.duration) - current_start

            # Don't merge if:
            # 1. Gap is too large
            # 2. Total duration would be too long
            # 3. Previous text ends with sentence ending punctuation
            if (gap > max_gap_ms or
                total_duration > max_duration_ms or
                (current_text and current_text.strip()[-1:] in sentence_endings)):
                should_merge = False

        if not should_merge and current_group:
            # Save current group as merged segment
            last_in_group = current_group[-1]
            merged_duration = (last_in_group.start + last_in_group.duration) - current_start

            merged.append(Subtitle(
                start=current_start,
                duration=merged_duration,
                text=current_text.strip()
            ))

            # Reset for new group
            current_group = []
            current_text = ""
            current_start = sub.start

        # Add to current group
        current_group.append(sub)
        if current_text:
            current_text += " " + sub.text
        else:
            current_text = sub.text

        # Map this subtitle to the merged segment index
        segment_mapping.append(len(merged))

    # Don't forget the last group
    if current_group:
        last_in_group = current_group[-1]
        merged_duration = (last_in_group.start + last_in_group.duration) - current_start

        merged.append(Subtitle(
            start=current_start,
            duration=merged_duration,
            text=current_text.strip()
        ))

    return merged, segment_mapping

@app.post("/translate-subtitles")
def translate_subtitles(request: TranslateSubtitlesRequest):
    """Translate subtitles while preserving timing information.

    Note: Subtitles should already be merged before calling this endpoint.
    The /subtitles endpoint returns merged subtitles automatically.
    """
    try:
        if not request.subtitles:
            return {
                "success": True,
                "count": 0,
                "translated_subtitles": []
            }

        print(f"🌐 Translating {len(request.subtitles)} subtitles to {request.to_lang}...")

        # Translate subtitles (already merged from /subtitles endpoint)
        texts_to_translate = [sub.text for sub in request.subtitles]

        # Translate in batches to avoid too large requests
        batch_size = 50
        all_translations = []

        for i in range(0, len(texts_to_translate), batch_size):
            batch = texts_to_translate[i:i+batch_size]
            translate_req = TranslateRequest(texts=batch, to_lang=request.to_lang, from_lang=request.from_lang)
            translate_result = translate_texts(translate_req)

            if translate_result['success']:
                all_translations.extend(translate_result['translations'])

        if len(all_translations) != len(request.subtitles):
            print(f"⚠️  Translation count mismatch: {len(all_translations)} != {len(request.subtitles)}")
            raise HTTPException(status_code=500, detail="Translation count mismatch")

        # Create translated subtitles with same timing
        translated_subtitles = []
        for i, sub in enumerate(request.subtitles):
            translated_subtitles.append({
                'start': sub.start,
                'duration': sub.duration,
                'text': all_translations[i]
            })

        print(f"✅ Successfully translated {len(translated_subtitles)} subtitles to {request.to_lang}")
        return {
            "success": True,
            "count": len(translated_subtitles),
            "to_lang": request.to_lang,
            "translated_subtitles": translated_subtitles
        }

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/translate")
def translate_texts(request: TranslateRequest):
    """Translate multiple texts using Microsoft Translator"""
    try:
        token = get_microsoft_translator_token()
        if not token:
            raise HTTPException(status_code=500, detail="Failed to get translation token")

        # Prepare request body
        body = [{"Text": text} for text in request.texts]

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'Referer': 'https://www.youtube.com/',
        }

        url = f'https://api-edge.cognitive.microsofttranslator.com/translate?from={request.from_lang}&to={request.to_lang}&api-version=3.0'

        response = requests.post(url, json=body, headers=headers, timeout=30)

        if response.status_code == 401:
            # Token expired, clear cache and retry
            global _translator_token_cache
            _translator_token_cache = None
            token = get_microsoft_translator_token()
            if token:
                headers['Authorization'] = f'Bearer {token}'
                response = requests.post(url, json=body, headers=headers, timeout=30)

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Translation failed: {response.text}")

        result = response.json()
        translations = [item['translations'][0]['text'] for item in result]

        return {
            "success": True,
            "count": len(translations),
            "translations": translations
        }

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
