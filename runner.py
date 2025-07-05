import os
import subprocess
import tempfile
import requests
import asyncio
import logging
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET")
SPRING_CALLBACK_URL = os.getenv("SPRING_CALLBACK_URL")

# Validate environment variables
if not all([SUPABASE_URL, SUPABASE_API_KEY, SUPABASE_BUCKET]):
    raise ValueError("Missing required environment variables: SUPABASE_URL, SUPABASE_API_KEY, SUPABASE_BUCKET")

subscribers = {}  # conversationId -> asyncio.Queue()

def extract_log(line: str) -> Optional[str]:
    """Extract meaningful log messages from manim output"""
    try:
        if "Animation" in line and "Partial" in line:
            m = re.search(r"Animation (\d+)", line)
            if m: 
                return f"Animation {m.group(1)} loaded"
        elif re.match(r".*Animation (\d+):.*?(\d+)%\|.*", line):
            m = re.search(r"Animation (\d+):.*?(\d+)%\|", line)
            if m: 
                return f"Animation {m.group(1)} progress: {m.group(2)}%"
        elif "File ready at" in line:
            return "Final video ready!"
        elif "Rendered ArchitectureDiagram" in line:
            return "Rendering complete!"
        elif "Played" in line:
            return line.strip()
        elif "ERROR" in line or "Exception" in line:
            return "Error occurred"
        return None
    except Exception as e:
        logger.error(f"Error extracting log: {e}")
        return None

def upload_to_supabase(file_path: str, filename: str) -> str:
    """Upload file to Supabase storage"""
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
            
        with open(file_path, 'rb') as f:
            headers = {
                "apikey": SUPABASE_API_KEY,
                "Authorization": f"Bearer {SUPABASE_API_KEY}",
                "Content-Type": "application/octet-stream",
                "x-upsert": "true"
            }
            upload_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{filename}"
            
            logger.info(f"Uploading {filename} to Supabase...")
            response = requests.post(upload_url, data=f, headers=headers, timeout=60)
            response.raise_for_status()
            
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{filename}"
            logger.info(f"Upload successful: {public_url}")
            return public_url
            
    except Exception as e:
        logger.error(f"Failed to upload to Supabase: {e}")
        raise

async def run_and_upload(conversation_id: str, code: str, json_data: dict) -> str:
    """Run manim animation and upload the result"""
    try:
        logger.info(f"Starting video generation for conversation: {conversation_id}")
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create Python file
            py_path = os.path.join(tmp_dir, f"{conversation_id}.py")
            with open(py_path, "w", encoding="utf-8") as f:
                f.write(code)
            
            # Create media directory
            media_dir = os.path.join(tmp_dir, "media")
            os.makedirs(media_dir, exist_ok=True)
            
            # Prepare manim command
            cmd = [
                "manim", 
                py_path, 
                "ArchitectureDiagram", 
                "--format", "mp4", 
                "-o", f"{conversation_id}.mp4",
                "--media_dir", media_dir,
                "-v", "INFO"  # Set verbosity level
            ]
            
            logger.info(f"Running command: {' '.join(cmd)}")
            
            # Send initial status
            if conversation_id in subscribers:
                await subscribers[conversation_id].put("Starting video generation...")
            
            # Run manim process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=tmp_dir
            )
            
            # Process output line by line
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                    
                line = line.decode('utf-8').strip()
                logger.info(f"Manim: {line}")
                
                # Extract and send meaningful updates
                log_message = extract_log(line)
                if log_message and conversation_id in subscribers:
                    try:
                        await subscribers[conversation_id].put(log_message)
                    except Exception as e:
                        logger.error(f"Failed to send update to subscriber: {e}")
            
            # Wait for process to complete
            return_code = await process.wait()
            if return_code != 0:
                error_msg = f"Manim process failed with return code {return_code}"
                logger.error(error_msg)
                raise subprocess.CalledProcessError(return_code, cmd)
            
            # Find the generated video file
            video_files = []
            for root, dirs, files in os.walk(media_dir):
                for file in files:
                    if file.endswith('.mp4'):
                        video_files.append(os.path.join(root, file))
            
            if not video_files:
                # Try alternative locations
                alt_locations = [
                    os.path.join(tmp_dir, f"{conversation_id}.mp4"),
                    os.path.join(tmp_dir, "media", "videos", f"{conversation_id}.mp4"),
                    os.path.join(tmp_dir, "media", "videos", "480p15", f"{conversation_id}.mp4"),
                    os.path.join(tmp_dir, "media", "videos", "720p30", f"{conversation_id}.mp4"),
                    os.path.join(tmp_dir, "media", "videos", "1080p60", f"{conversation_id}.mp4")
                ]
                
                for location in alt_locations:
                    if os.path.exists(location):
                        video_files.append(location)
                        break
            
            if not video_files:
                error_msg = "No video file was generated"
                logger.error(error_msg)
                logger.error(f"Files in tmp_dir: {os.listdir(tmp_dir)}")
                if os.path.exists(media_dir):
                    logger.error(f"Files in media_dir: {os.listdir(media_dir)}")
                raise FileNotFoundError(error_msg)
            
            # Use the first video file found
            video_path = video_files[0]
            logger.info(f"Found video file: {video_path}")
            
            # Send upload status
            if conversation_id in subscribers:
                await subscribers[conversation_id].put("Uploading video...")
            
            # Upload to Supabase
            video_url = upload_to_supabase(video_path, f"{conversation_id}.mp4")
            
            # Send completion status
            if conversation_id in subscribers:
                await subscribers[conversation_id].put("Video generation completed!")
            
            # Notify Spring backend if configured
            if SPRING_CALLBACK_URL:
                try:
                    logger.info("Notifying Spring backend...")
                    callback_response = requests.post(
                        SPRING_CALLBACK_URL, 
                        json={
                            "conversationId": conversation_id,
                            "videoUrl": video_url
                        },
                        timeout=30
                    )
                    callback_response.raise_for_status()
                    logger.info("Successfully notified Spring backend")
                except Exception as e:
                    logger.error(f"Failed to notify Spring backend: {e}")
                    # Don't fail the entire process if callback fails
            
            return video_url
            
    except Exception as e:
        logger.error(f"Error in run_and_upload: {e}")
        
        # Send error to subscribers
        if conversation_id in subscribers:
            try:
                await subscribers[conversation_id].put(f"Error: {str(e)}")
            except:
                pass
        
        raise