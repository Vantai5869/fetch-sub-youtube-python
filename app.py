from fastapi import FastAPI, HTTPException
import yt_dlp
import json
import os
import tempfile

app = FastAPI(title="YouTube Subtitle Fetcher API")

@app.get("/subtitles")
def get_subtitles(video_id: str, lang: str = "ko"):
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Use a temporary directory for yt-dlp files
    with tempfile.TemporaryDirectory() as tmpdir:
        output_tmpl = os.path.join(tmpdir, "sub")
        
        ydl_opts = {
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [lang],
            # Do not force json3 here to avoid "format not available" error
            # 'subtitlesformat': 'json3', 
            'outtmpl': output_tmpl,
            'quiet': True,
            'no_warnings': True,
        }

        # Check if cookies.txt exists and use it
        if os.path.exists("cookies.txt"):
            ydl_opts['cookiefile'] = 'cookies.txt'
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Get info first to check available formats
                info = ydl.extract_info(video_url, download=False)
                
                # Download
                ydl.download([video_url])
                
                # Find the file created by yt-dlp
                # Files are named like: tmpdir/sub.lang.ext
                downloaded_files = os.listdir(tmpdir)
                filename = None
                
                # Priority: .json3 > .srv3 > .srv2 > .srv1 > .vtt > .ttml
                ext_priority = ['.json3', '.srv3', '.srv2', '.srv1', '.vtt', '.ttml']
                for ext in ext_priority:
                    target = f"sub.{lang}{ext}"
                    if target in downloaded_files:
                        filename = os.path.join(tmpdir, target)
                        break
                
                # If specifically requested language not found, maybe it's auto-generated with a different suffix
                if not filename:
                    for f in downloaded_files:
                        if f.startswith("sub."):
                            filename = os.path.join(tmpdir, f)
                            break

                if filename and os.path.exists(filename):
                    # For now, we only support parsing json3 directly
                    if filename.endswith(".json3"):
                        with open(filename, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        
                        subtitles = []
                        for event in data.get('events', []):
                            if 'segs' in event:
                                text = "".join([s.get('utf8', '') for s in event['segs']]).strip()
                                if text:
                                    subtitles.append({
                                        'start': event.get('tStartMs', 0),
                                        'duration': event.get('dDurationMs', 0),
                                        'text': text
                                    })
                        
                        return {
                            "success": True,
                            "video_id": video_id,
                            "language": lang,
                            "format": "json3",
                            "count": len(subtitles),
                            "subtitles": subtitles
                        }
                    else:
                        # Return raw content for other formats (user can parse on frontend)
                        with open(filename, "r", encoding="utf-8") as f:
                            content = f.read()
                        return {
                            "success": True,
                            "video_id": video_id,
                            "language": lang,
                            "format": filename.split('.')[-1],
                            "raw_content": content
                        }
                else:
                    raise HTTPException(status_code=404, detail=f"No subtitles found for video '{video_id}' in language '{lang}'")
                    
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
