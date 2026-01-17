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
            'subtitlesformat': 'json3',
            'outtmpl': output_tmpl,
            'quiet': True,
            'no_warnings': True,
        }

        # Check if cookies.txt exists and use it
        if os.path.exists("cookies.txt"):
            ydl_opts['cookiefile'] = 'cookies.txt'
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                ydl.download([video_url])
                
                # yt-dlp typically saves as: output_tmpl.lang.json3
                filename = f"{output_tmpl}.{lang}.json3"
                
                if os.path.exists(filename):
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
                        "count": len(subtitles),
                        "subtitles": subtitles
                    }
                else:
                    raise HTTPException(status_code=404, detail=f"No subtitles found for language '{lang}'")
                    
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
