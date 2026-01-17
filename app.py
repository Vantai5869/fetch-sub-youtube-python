from fastapi import FastAPI, HTTPException
import yt_dlp
import json
import os
import tempfile

app = FastAPI(title="YouTube Subtitle Fetcher API")

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
                    
                    return {
                        "success": True,
                        "video_id": video_id,
                        "language": lang,
                        "format": "json3",
                        "count": len(parsed_subtitles),
                        "subtitles": parsed_subtitles
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

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
