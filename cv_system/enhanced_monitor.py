import cv2
import numpy as np
import json
import datetime
from collections import deque
import os
import threading
import time
import requests
from typing import List, Dict, Any, Optional
import logging
from dataclasses import dataclass
import asyncio
import websockets

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class ShelfConfig:
    id: int
    name: str
    region: List[int]
    camera_id: int
    empty_threshold: float = 0.15
    product_category: str = ""

class EnhancedStockMonitor:
    def __init__(self, camera_id=0, api_base_url="http://localhost:8000", auth_token=""):
        self.camera_id = camera_id
        self.cap = None
        self.api_base_url = api_base_url
        self.auth_token = auth_token
        self.headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        
        # Configuration
        self.shelf_configs: List[ShelfConfig] = []
        self.empty_threshold = 0.15
        self.alert_history = deque(maxlen=100)
        self.setup_mode = False
        self.monitoring = False
        self.running = False
        
        # Computer vision components
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=16, detectShadows=True
        )
        
        # Alert system
        self.alert_cooldown = {}
        self.alert_duration = 300  # 5 minutes cooldown
        
        # Performance metrics
        self.frame_count = 0
        self.fps = 0
        self.last_fps_time = time.time()
        
        # WebSocket for real-time updates
        self.websocket_url = api_base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        self.websocket = None
        
    def initialize_camera(self, camera_source):
        """Initialize camera with various source types"""
        try:
            # Try different camera sources
            if isinstance(camera_source, int):
                self.cap = cv2.VideoCapture(camera_source)
            elif isinstance(camera_source, str):
                if camera_source.startswith(('rtsp://', 'http://', 'https://')):
                    self.cap = cv2.VideoCapture(camera_source)
                elif os.path.exists(camera_source):
                    self.cap = cv2.VideoCapture(camera_source)
                else:
                    raise ValueError(f"Invalid camera source: {camera_source}")
            
            if self.cap is None or not self.cap.isOpened():
                raise ValueError("Failed to open camera")
            
            # Set camera properties for better performance
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            
            logger.info(f"Camera initialized successfully: {camera_source}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize camera: {str(e)}")
            return False
    
    def load_shelves_from_api(self):
        """Load shelf configurations from API"""
        try:
            response = requests.get(
                f"{self.api_base_url}/api/shelves",
                headers=self.headers,
                params={"camera_id": self.camera_id}
            )
            
            if response.status_code == 200:
                shelves_data = response.json()
                self.shelf_configs = [
                    ShelfConfig(
                        id=shelf["id"],
                        name=shelf["name"],
                        region=shelf["region"],
                        camera_id=shelf["camera_id"],
                        empty_threshold=shelf.get("empty_threshold", 0.15),
                        product_category=shelf.get("product_category", "")
                    )
                    for shelf in shelves_data
                ]
                logger.info(f"Loaded {len(self.shelf_configs)} shelves from API")
                return True
            else:
                logger.error(f"Failed to load shelves: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error loading shelves from API: {str(e)}")
            return False
    
    def detect_shelves_automatically(self, frame):
        """Enhanced automatic shelf detection"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Apply Gaussian blur
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Edge detection with multiple thresholds
        edges1 = cv2.Canny(blurred, 50, 150)
        edges2 = cv2.Canny(blurred, 30, 100)
        edges = cv2.bitwise_or(edges1, edges2)
        
        # Morphological operations to connect edges
        kernel = np.ones((3, 3), np.uint8)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
        
        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        potential_shelves = []
        
        for contour in contours:
            # Calculate contour area and bounding rectangle
            area = cv2.contourArea(contour)
            if area < 5000:  # Filter small contours
                continue
                
            x, y, w, h = cv2.boundingRect(contour)
            
            # Filter based on aspect ratio and size
            aspect_ratio = w / h
            if aspect_ratio > 1.5 and w > 150 and h > 50:
                # Calculate confidence based on various factors
                confidence = min(area / 10000, 1.0) * min(aspect_ratio / 3.0, 1.0)
                
                potential_shelves.append({
                    'region': [x, y, w, h],
                    'area': area,
                    'aspect_ratio': aspect_ratio,
                    'confidence': confidence
                })
        
        # Sort by confidence and return top candidates
        potential_shelves.sort(key=lambda x: x['confidence'], reverse=True)
        return potential_shelves[:10]  # Return top 10 candidates
    
    def analyze_shelf_occupancy_advanced(self, frame, shelf_region):
        """Advanced shelf occupancy analysis with multiple techniques"""
        x, y, w, h = shelf_region
        
        # Validate region
        if x < 0 or y < 0 or x + w > frame.shape[1] or y + h > frame.shape[0]:
            return 0.0, None
            
        shelf_roi = frame[y:y+h, x:x+w]
        
        if shelf_roi.size == 0:
            return 0.0, shelf_roi
        
        # Convert to different color spaces
        gray_roi = cv2.cvtColor(shelf_roi, cv2.COLOR_BGR2GRAY)
        hsv_roi = cv2.cvtColor(shelf_roi, cv2.COLOR_BGR2HSV)
        lab_roi = cv2.cvtColor(shelf_roi, cv2.COLOR_BGR2LAB)
        
        # Method 1: Enhanced edge density analysis
        edges = cv2.Canny(gray_roi, 50, 150)
        edge_density = np.sum(edges > 0) / (edges.shape[0] * edges.shape[1])
        
        # Method 2: Multi-channel color variance
        gray_variance = np.var(gray_roi)
        color_variance = np.mean([np.var(hsv_roi[:,:,i]) for i in range(3)])
        
        # Method 3: Texture analysis using Local Binary Patterns
        texture_score = self.calculate_texture_score(gray_roi)
        
        # Method 4: Advanced histogram analysis
        hist_score = self.calculate_histogram_score(gray_roi)
        
        # Method 5: Background subtraction
        try:
            fg_mask = self.bg_subtractor.apply(shelf_roi)
            foreground_ratio = np.sum(fg_mask > 0) / (fg_mask.shape[0] * fg_mask.shape[1])
        except:
            foreground_ratio = 0.0
        
        # Method 6: Contour complexity analysis
        contour_score = self.calculate_contour_complexity(edges)
        
        # Method 7: Color distribution analysis
        color_dist_score = self.calculate_color_distribution_score(hsv_roi)
        
        # Combine all metrics with weights
        occupancy_score = (
            edge_density * 0.20 +
            min(gray_variance / 1000, 1.0) * 0.15 +
            min(color_variance / 1000, 1.0) * 0.15 +
            texture_score * 0.15 +
            hist_score * 0.10 +
            foreground_ratio * 0.10 +
            contour_score * 0.10 +
            color_dist_score * 0.05
        )
        
        return min(occupancy_score, 1.0), shelf_roi
    
    def calculate_texture_score(self, gray_roi):
        """Calculate texture score using variance of Laplacian"""
        try:
            laplacian = cv2.Laplacian(gray_roi, cv2.CV_64F)
            texture_score = np.var(laplacian) / 10000
            return min(texture_score, 1.0)
        except:
            return 0.0
    
    def calculate_histogram_score(self, gray_roi):
        """Calculate histogram-based score"""
        try:
            hist = cv2.calcHist([gray_roi], [0], None, [256], [0, 256])
            hist_variance = np.var(hist)
            hist_entropy = -np.sum(hist * np.log2(hist + 1e-10))
            return min((hist_variance / 1000000 + hist_entropy / 1000), 1.0)
        except:
            return 0.0
    
    def calculate_contour_complexity(self, edges):
        """Calculate contour complexity score"""
        try:
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return 0.0
            
            # Calculate complexity based on contour count and perimeter
            total_perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
            complexity = min(len(contours) / 20.0 + total_perimeter / 1000.0, 1.0)
            return complexity
        except:
            return 0.0
    
    def calculate_color_distribution_score(self, hsv_roi):
        """Calculate color distribution score"""
        try:
            # Calculate color distribution in HSV space
            h_hist = cv2.calcHist([hsv_roi], [0], None, [180], [0, 180])
            s_hist = cv2.calcHist([hsv_roi], [1], None, [256], [0, 256])
            
            # Calculate distribution entropy
            h_entropy = -np.sum(h_hist * np.log2(h_hist + 1e-10))
            s_entropy = -np.sum(s_hist * np.log2(s_hist + 1e-10))
            
            return min((h_entropy + s_entropy) / 2000, 1.0)
        except:
            return 0.0
    
    def send_alert_to_api(self, shelf_config: ShelfConfig, occupancy_score: float):
        """Send alert to API backend"""
        try:
            # Determine priority
            if occupancy_score < 0.05:
                priority = "HIGH"
            elif occupancy_score < shelf_config.empty_threshold:
                priority = "MEDIUM"
            else:
                priority = "LOW"
            
            # Create alert data
            alert_data = {
                "shelf_id": shelf_config.id,
                "priority": priority,
                "message": f"ALERT: {shelf_config.name} is empty and needs refilling!",
                "occupancy_score": occupancy_score
            }
            
            # Send to API
            response = requests.post(
                f"{self.api_base_url}/api/alerts",
                json=alert_data,
                headers=self.headers
            )
            
            if response.status_code == 200:
                logger.info(f"Alert sent successfully for shelf {shelf_config.name}")
                return True
            else:
                logger.error(f"Failed to send alert: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending alert to API: {str(e)}")
            return False
    
    def update_fps(self):
        """Update FPS counter"""
        self.frame_count += 1
        current_time = time.time()
        
        if current_time - self.last_fps_time > 1.0:
            self.fps = self.frame_count / (current_time - self.last_fps_time)
            self.frame_count = 0
            self.last_fps_time = current_time
    
    def draw_enhanced_interface(self, frame):
        """Draw enhanced monitoring interface"""
        height, width = frame.shape[:2]
        
        # Draw semi-transparent overlay
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (width, 80), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
        
        # Draw title
        cv2.putText(frame, "Enhanced Stock Monitoring AI", (10, 25), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        # Draw status
        status_text = "SETUP MODE" if self.setup_mode else "MONITORING" if self.monitoring else "STANDBY"
        status_color = (0, 255, 255) if self.setup_mode else (0, 255, 0) if self.monitoring else (0, 0, 255)
        cv2.putText(frame, status_text, (10, 50), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)
        
        # Draw metrics
        cv2.putText(frame, f"FPS: {self.fps:.1f}", (width - 200, 25), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        cv2.putText(frame, f"Shelves: {len(self.shelf_configs)}", (width - 200, 45), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        
        # Draw recent alerts count
        recent_alerts = len([a for a in self.alert_history 
                           if (datetime.datetime.now() - a.get('timestamp', datetime.datetime.now())).seconds < 3600])
        cv2.putText(frame, f"Alerts (1h): {recent_alerts}", (width - 200, 65), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        
        return frame
    
    def draw_shelf_analysis(self, frame):
        """Draw shelf analysis with enhanced visualization"""
        for shelf_config in self.shelf_configs:
            x, y, w, h = shelf_config.region
            
            # Analyze occupancy
            occupancy_score, shelf_roi = self.analyze_shelf_occupancy_advanced(frame, shelf_config.region)
            
            # Determine color and status
            if occupancy_score < shelf_config.empty_threshold:
                color = (0, 0, 255)  # Red for empty
                status = "EMPTY"
                thickness = 3
                if self.monitoring:
                    self.send_alert_to_api(shelf_config, occupancy_score)
            elif occupancy_score < 0.3:
                color = (0, 165, 255)  # Orange for low stock
                status = "LOW"
                thickness = 2
            elif occupancy_score < 0.7:
                color = (0, 255, 255)  # Yellow for medium stock
                status = "MEDIUM"
                thickness = 2
            else:
                color = (0, 255, 0)  # Green for stocked
                status = "STOCKED"
                thickness = 2
            
            # Draw enhanced rectangle with rounded corners effect
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, thickness)
            
            # Draw shelf info with background
            label = f"{shelf_config.name} ({status})"
            label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
            cv2.rectangle(frame, (x, y - 25), (x + label_size[0] + 10, y - 5), color, -1)
            cv2.putText(frame, label, (x + 5, y - 10), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            
            # Draw occupancy score and confidence bar
            score_text = f"{occupancy_score:.3f}"
            cv2.putText(frame, score_text, (x, y + h + 15), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
            
            # Draw confidence bar
            bar_width = w
            bar_height = 5
            bar_fill = int(occupancy_score * bar_width)
            cv2.rectangle(frame, (x, y + h + 20), (x + bar_width, y + h + 20 + bar_height), (100, 100, 100), -1)
            cv2.rectangle(frame, (x, y + h + 20), (x + bar_fill, y + h + 20 + bar_height), color, -1)
        
        return frame
    
    def run_monitoring_loop(self, camera_source):
        """Main monitoring loop with enhanced features"""
        if not self.initialize_camera(camera_source):
            logger.error("Failed to initialize camera")
            return
        
        # Load shelf configurations
        if not self.load_shelves_from_api():
            logger.warning("Failed to load shelves from API, using local configuration")
        
        self.running = True
        self.monitoring = True
        
        logger.info("Starting enhanced monitoring loop")
        
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                logger.error("Failed to read frame")
                break
            
            # Update FPS
            self.update_fps()
            
            # Process frame
            frame = self.draw_enhanced_interface(frame)
            frame = self.draw_shelf_analysis(frame)
            
            # Display frame
            cv2.imshow('Enhanced Stock Monitoring', frame)
            
            # Handle key presses
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                self.running = False
            elif key == ord('p'):
                self.monitoring = not self.monitoring
                status = "RESUMED" if self.monitoring else "PAUSED"
                logger.info(f"Monitoring {status}")
            elif key == ord('r'):
                # Reset background subtractor
                self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
                    history=500, varThreshold=16, detectShadows=True
                )
                logger.info("Background model reset")
            elif key == ord('s'):
                # Save screenshot
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"screenshot_{timestamp}.jpg"
                cv2.imwrite(filename, frame)
                logger.info(f"Screenshot saved: {filename}")
        
        self.cleanup()
    
    def cleanup(self):
        """Cleanup resources"""
        self.running = False
        if self.cap:
            self.cap.release()
        cv2.destroyAllWindows()
        logger.info("Enhanced monitoring system shutdown complete")

# Example usage
if __name__ == "__main__":
    # Initialize the enhanced monitoring system
    monitor = EnhancedStockMonitor(
        camera_id=1,
        api_base_url="http://localhost:8000",
        auth_token="your-auth-token-here"
    )
    
    # Run with video file or camera
    # monitor.run_monitoring_loop("C:/Users/Asquare/Downloads/CCTV_Super_Mart_Video_Prompt.mp4")
    monitor.run_monitoring_loop(0)  # Use webcam
