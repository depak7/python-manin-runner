from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from runner import run_and_upload, subscribers
import asyncio
import uvicorn
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

class RunRequest(BaseModel):
    conversation_id: str
    code: str
    json_data: dict = {}

@app.get("/render/logs/stream")
async def sse(conversationId: str):
    """Server-Sent Events endpoint for streaming logs and closing connection after completion"""
    try:
        async def event_gen():
            q = asyncio.Queue()
            subscribers[conversationId] = q
            try:
                logger.info(f"Starting SSE stream for conversation: {conversationId}")
                while True:
                    try:
                        # Wait for message with timeout to prevent hanging
                        msg = await asyncio.wait_for(q.get(), timeout=30.0)

                        # Send message
                        yield f"data: {msg} \n\n"

                        # Check for shutdown signal
                        if msg.strip().lower() == "video generation completed!":
                            logger.info(f"Received completion signal for {conversationId}, closing stream.")
                            break  # Exit generator to close connection
                        elif msg.lower().startswith("error:"):
                            logger.info(f"Received error signal for {conversationId}, closing stream.")
                            break
                    except asyncio.TimeoutError:
                        yield f'data: {{"data": "keepalive"}}\n\n'
            except Exception as e:
                logger.error(f"Error in event generator: {e}")
                yield f'data: {{"data": "Error: {str(e)}"}}\n\n'
            finally:
                logger.info(f"Cleaning up SSE stream for conversation: {conversationId}")
                subscribers.pop(conversationId, None)

        return StreamingResponse(
            event_gen(), 
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"  # Disable nginx buffering
            }
        )
    except Exception as e:
        logger.error(f"Error setting up SSE stream: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run")
async def run(req: RunRequest):
    """Run manim code and return video URL"""
    try:
        if not req.conversation_id or not req.code:
            raise HTTPException(status_code=400, detail="conversation_id and code are required")
        
        logger.info(f"Starting video generation for conversation: {req.conversation_id}")
        
        # Run the video generation and wait for completion
        url = await run_and_upload(req.conversation_id, req.code, req.json_data)
        
        return {
            "status": "success", 
            "url": url,
            "conversation_id": req.conversation_id
        }
        
    except Exception as e:
        logger.error(f"Error in run endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Remove the wrapper function since we're not using background tasks anymore

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "active_streams": len(subscribers)}

@app.on_event("startup")
async def startup_event():
    logger.info("Starting Manim Video Generator API")

@app.on_event("shutdown") 
async def shutdown_event():
    logger.info("Shutting down Manim Video Generator API")
    # Clean up any remaining subscribers
    subscribers.clear()

if __name__ == "__main__":
    print("Starting server...")
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=False)