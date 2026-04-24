import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func

from database import get_session, check_db_connection
from models import Worker, Channel, BulkImportLog

app = FastAPI(title="Telegram Reaction Master")

# Make sure templates directory exists
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(templates_dir, exist_ok=True)
templates = Jinja2Templates(directory=templates_dir)

@app.get("/health")
async def health_check():
    """Endpoint for UptimeRobot to ping."""
    db_ok = await check_db_connection()
    
    active_workers = 0
    if db_ok:
        try:
            async with get_session() as session:
                active_workers = await session.scalar(
                    select(func.count(Worker.id)).where(Worker.is_active == True)
                )
        except Exception:
            pass

    return {
        "status": "ok" if db_ok else "error",
        "db_connected": db_ok,
        "active_workers": active_workers
    }

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Simple dashboard showing bots and their status."""
    db_ok = await check_db_connection()
    workers_data = []
    bulk_logs_data = []
    
    if db_ok:
        try:
            async with get_session() as session:
                result = await session.execute(select(Worker))
                workers = result.scalars().all()
                
                for w in workers:
                    # Count channels for this worker
                    channel_count = len(w.channels)
                    workers_data.append({
                        "username": w.username or "Unknown",
                        "status": "Active" if w.is_active else "Offline",
                        "status_class": "active" if w.is_active else "offline",
                        "channels": channel_count
                    })
                
                # Fetch recent bulk imports
                bulk_stmt = select(BulkImportLog).order_by(BulkImportLog.id.desc()).limit(5)
                bulk_res = await session.execute(bulk_stmt)
                bulk_logs = bulk_res.scalars().all()
                
                for log in bulk_logs:
                    bulk_logs_data.append({
                        "id": log.id,
                        "status": log.status,
                        "total": log.total_tokens,
                        "success": log.success_count,
                        "failed": log.failed_count,
                        "date": log.started_at.strftime("%Y-%m-%d %H:%M:%S")
                    })
        except Exception as e:
            print(f"Error fetching dashboard data: {e}")

    return templates.TemplateResponse(
        "dashboard.html", 
        {
            "request": request, 
            "workers": workers_data, 
            "db_status": "Connected" if db_ok else "Disconnected",
            "bulk_logs": bulk_logs_data
        }
    )
