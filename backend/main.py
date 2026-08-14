
import time
import logging
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.supabase_client import SUPABASE_KEY_ROLE, SUPABASE_PROJECT_URL
from app.runtime import get_cors_allowed_origins
from app.routes import router
from app.waitlist import waitlist_router
from connection.websocket import socket_router
from pages.profile import profile_router
from pages.dashboard import dashboard_router
from pages import recent_sessions
from pages.courses import course_router
from app.subscriptions import subscription_router
from core.cron import register_background_tasks,start_background_tasks,stop_background_tasks


# 1. Initialize Python's built-in logging tool formatting style
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KamaraLogger")


# check if the frontend can send raw audio and if it is also configured to receive audio
app = FastAPI(title="AI Agentic Microservice")

# add kamara url

KEEP_ALIVE_URL = os.environ.get("KEEP_ALIVE_URL", "https://kamara.onrender.com/health")
KEEP_ALIVE_INTERVAL_SECONDS = int(os.environ.get("KEEP_ALIVE_INTERVAL_SECONDS", 600))
ENABLE_KEEP_ALIVE = os.environ.get("ENABLE_KEEP_ALIVE", "true").lower() in ("1", "true", "yes")

app.state.keepalive_task = None

# Cross-Origin resource allowances so React client can fetch records securely
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup() -> None:
    await start_background_tasks(app)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await stop_background_tasks(app)



# 2. Add the Global Tracking Middleware Interceptor
@app.middleware("http")
async def log_incoming_requests(request: Request, call_next):
    # This block executes the moment React flings a packet over the network
    start_time = time.time()
    
    logger.info(f"🚀 INCOMING REQUEST: {request.method} -> {request.url.path}")
    
    # Process the request and proceed to your routes
    response = await call_next(request)
    
    # This block executes right before sending data back to React
    process_time = (time.time() - start_time) * 1000
    logger.info(f"✅ COMPLETED REQUEST: {request.method} -> {request.url.path} | Status: {response.status_code} | Time: {process_time:.2f}ms\n")
    
    return response

# add ping for render


# 🚨 ADD THIS GET REQUEST CONFIRMATION ROUTE HER E:
@app.get("/")
@app.get("/health")
@app.get("/health-check")
@app.get("/api/v1/health-check")
async def health_check():
    """ A completely open public GET endpoint that returns a test dictionary """
    return {
        "status": "online",
        "message": "FastAPI is working perfectly!",
        "server_status": "healthy"
    }


@app.get("/api/v1/debug/config")
async def debug_config():
    return {
        "supabase_url": SUPABASE_PROJECT_URL,
        "supabase_key_role": SUPABASE_KEY_ROLE,
        "has_gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
        "cloud_run_service": os.getenv("K_SERVICE"),
    }


app.include_router(router)
app.include_router(waitlist_router)
app.include_router(profile_router)
app.include_router(dashboard_router)
app.include_router(course_router)
app.include_router(subscription_router)
app.include_router(socket_router) # websocket router

if __name__ == "__main__":
    import uvicorn
    import os

    # 1. Fall back to 8001 locally, but let Cloud Run or Render inject the proper port
    port = int(os.environ.get("PORT", 8001))
    
    # 2. Check for Google Cloud Run (K_SERVICE) or Render (RENDER) production tokens
    is_cloud_run = os.environ.get("K_SERVICE") is not None
    is_render = os.environ.get("RENDER") is not None
    
    # 3. Disable reload if the app detects either production environment
    if is_cloud_run or is_render:
        reload_setting = False
    else:
        reload_setting = True

    print(f"Booting server on port {port} | Production Mode: {is_cloud_run or is_render}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload_setting)









