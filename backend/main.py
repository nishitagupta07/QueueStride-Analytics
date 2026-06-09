from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List, Optional, Dict
import asyncio
import json
import cv2
import numpy as np
from datetime import datetime, timedelta
import base64
from io import BytesIO
from PIL import Image
import os
import logging

from database import get_db, engine
from models import *
from schemas import *
from auth import create_access_token, verify_token, get_current_user, hash_password, verify_password
from cv_processor import CVProcessor
from notification_system import NotificationSystem

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Automated Stock Monitoring API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static uploads files
app.mount("/uploads", StaticFiles(directory="static"), name="uploads")

@app.get("/")
async def root():
    return {
        "title": "Automated Stock Monitoring API",
        "version": "1.0.0",
        "documentation": "/docs",
        "health": "/health",
        "status": "running"
    }

# Initialize systems
cv_processor = CVProcessor()
notification_system = NotificationSystem()

# Security
security = HTTPBearer()

# WebSocket manager for real-time updates
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)
    
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

# Authentication endpoints
@app.post("/api/auth/register", response_model=UserResponse)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    # Check if user exists
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    hashed_password = hash_password(user.password)
    db_user = User(
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        hashed_password=hashed_password,
        role=user.role
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return db_user

@app.post("/api/auth/login")
async def login(user: UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.email == user.email).first()
    if not db_user or not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    access_token = create_access_token(data={"sub": db_user.email})
    return {"access_token": access_token, "token_type": "bearer", "user": db_user}

# Store endpoints
@app.post("/api/stores", response_model=StoreResponse)
async def create_store(store: StoreCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_store = Store(**store.dict(), owner_id=current_user.id)
    db.add(db_store)
    db.commit()
    db.refresh(db_store)
    return db_store

@app.get("/api/stores", response_model=List[StoreResponse])
async def get_stores(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    stores = db.query(Store).filter(Store.owner_id == current_user.id).offset(skip).limit(limit).all()
    return stores

@app.get("/api/stores/{store_id}", response_model=StoreResponse)
async def get_store(store_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id, Store.owner_id == current_user.id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    return store

# Camera endpoints
@app.post("/api/cameras", response_model=CameraResponse)
async def create_camera(camera: CameraCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify store ownership
    store = db.query(Store).filter(Store.id == camera.store_id, Store.owner_id == current_user.id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    
    db_camera = Camera(**camera.dict())
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera

@app.get("/api/cameras", response_model=List[CameraResponse])
async def get_cameras(store_id: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(Camera).join(Store).filter(Store.owner_id == current_user.id)
    if store_id:
        query = query.filter(Camera.store_id == store_id)
    cameras = query.all()
    return cameras

@app.get("/api/cameras/{camera_id}", response_model=CameraResponse)
async def get_camera(camera_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    camera = db.query(Camera).join(Store).filter(
        Camera.id == camera_id, 
        Store.owner_id == current_user.id
    ).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera

@app.put("/api/cameras/{camera_id}/status")
async def update_camera_status(camera_id: int, status: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    camera = db.query(Camera).join(Store).filter(
        Camera.id == camera_id, 
        Store.owner_id == current_user.id
    ).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    camera.status = status
    db.commit()
    return {"message": "Camera status updated"}

# Shelf endpoints
@app.post("/api/shelves", response_model=ShelfResponse)
async def create_shelf(shelf: ShelfCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify camera ownership
    camera = db.query(Camera).join(Store).filter(
        Camera.id == shelf.camera_id, 
        Store.owner_id == current_user.id
    ).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    db_shelf = Shelf(**shelf.dict())
    db.add(db_shelf)
    db.commit()
    db.refresh(db_shelf)
    return db_shelf

@app.get("/api/shelves", response_model=List[ShelfResponse])
async def get_shelves(camera_id: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(Shelf).join(Camera).join(Store).filter(Store.owner_id == current_user.id)
    if camera_id:
        query = query.filter(Shelf.camera_id == camera_id)
    shelves = query.all()
    return shelves

@app.delete("/api/shelves/{shelf_id}")
async def delete_shelf(shelf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    shelf = db.query(Shelf).join(Camera).join(Store).filter(
        Shelf.id == shelf_id, 
        Store.owner_id == current_user.id
    ).first()
    if not shelf:
        raise HTTPException(status_code=404, detail="Shelf not found")
    
    db.delete(shelf)
    db.commit()
    return {"message": "Shelf deleted"}

# Alert endpoints
@app.get("/api/alerts", response_model=List[AlertResponse])
async def get_alerts(
    skip: int = 0, 
    limit: int = 100, 
    shelf_id: Optional[int] = None,
    priority: Optional[str] = None,
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    query = db.query(Alert).join(Shelf).join(Camera).join(Store).filter(Store.owner_id == current_user.id)
    
    if shelf_id:
        query = query.filter(Alert.shelf_id == shelf_id)
    if priority:
        query = query.filter(Alert.priority == priority)
    
    alerts = query.order_by(Alert.created_at.desc()).offset(skip).limit(limit).all()
    return alerts

@app.post("/api/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    alert = db.query(Alert).join(Shelf).join(Camera).join(Store).filter(
        Alert.id == alert_id, 
        Store.owner_id == current_user.id
    ).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    alert.acknowledged = True
    alert.acknowledged_at = datetime.utcnow()
    alert.acknowledged_by = current_user.id
    db.commit()
    return {"message": "Alert acknowledged"}

# Analytics endpoints
@app.get("/api/analytics/dashboard")
async def get_dashboard_analytics(
    store_id: Optional[int] = None,
    days: int = 7,
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    # Get date range
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    # Base query
    base_query = db.query(Store).filter(Store.owner_id == current_user.id)
    if store_id:
        base_query = base_query.filter(Store.id == store_id)
    
    # Get stores
    stores = base_query.all()
    store_ids = [store.id for store in stores]
    
    # Get cameras count
    cameras_count = db.query(Camera).filter(Camera.store_id.in_(store_ids)).count()
    
    # Get shelves count
    shelves_count = db.query(Shelf).join(Camera).filter(Camera.store_id.in_(store_ids)).count()
    
    # Get alerts count
    alerts_query = db.query(Alert).join(Shelf).join(Camera).filter(
        Camera.store_id.in_(store_ids),
        Alert.created_at >= start_date
    )
    total_alerts = alerts_query.count()
    high_priority_alerts = alerts_query.filter(Alert.priority == "HIGH").count()
    
    # Get alerts by day
    alerts_by_day = []
    for i in range(days):
        day_start = start_date + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_alerts = alerts_query.filter(
            Alert.created_at >= day_start,
            Alert.created_at < day_end
        ).count()
        alerts_by_day.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "alerts": day_alerts
        })
    
    return {
        "stores_count": len(stores),
        "cameras_count": cameras_count,
        "shelves_count": shelves_count,
        "total_alerts": total_alerts,
        "high_priority_alerts": high_priority_alerts,
        "alerts_by_day": alerts_by_day
    }

# Computer Vision endpoints
@app.post("/api/cv/process-frame")
async def process_frame(
    camera_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify camera ownership
    camera = db.query(Camera).join(Store).filter(
        Camera.id == camera_id, 
        Store.owner_id == current_user.id
    ).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    # Read image
    image_data = await file.read()
    image = Image.open(BytesIO(image_data))
    frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    
    # Get shelves for this camera
    shelves = db.query(Shelf).filter(Shelf.camera_id == camera_id).all()
    
    # Process frame
    results = cv_processor.process_frame(frame, shelves)
    
    # Save alerts if any
    for result in results:
        if result['needs_alert']:
            alert = Alert(
                shelf_id=result['shelf_id'],
                priority=result['priority'],
                message=result['message'],
                occupancy_score=result['occupancy_score']
            )
            db.add(alert)
    
    if any(result['needs_alert'] for result in results):
        db.commit()
        # Send real-time notification
        await manager.broadcast(json.dumps({
            "type": "alert",
            "camera_id": camera_id,
            "results": results
        }))
    
    return {"results": results}

@app.post("/api/cv/detect-shelves")
async def detect_shelves(
    camera_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify camera ownership
    camera = db.query(Camera).join(Store).filter(
        Camera.id == camera_id, 
        Store.owner_id == current_user.id
    ).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    # Read image
    image_data = await file.read()
    image = Image.open(BytesIO(image_data))
    frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    
    # Detect shelves
    detected_shelves = cv_processor.detect_shelves(frame)
    
    return {"detected_shelves": detected_shelves}

# WebSocket endpoint for real-time updates
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle incoming messages if needed
            await manager.send_personal_message(f"Message received: {data}", websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Health check
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow()}

# Serve React Frontend SPA
from fastapi.responses import FileResponse
frontend_path = os.path.join(os.path.dirname(__file__), "frontend_build")

@app.get("/{catchall:path}")
async def serve_react_app(catchall: str):
    file_path = os.path.join(frontend_path, catchall)
    if catchall and os.path.isfile(file_path):
        return FileResponse(file_path)
    # Default to index.html for SPA routing (e.g. /login, /dashboard)
    index_path = os.path.join(frontend_path, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"message": "Backend API is running. Frontend build not found."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
