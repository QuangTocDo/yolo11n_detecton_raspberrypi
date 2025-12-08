import cv2
import time
import numpy as np
from ultralytics import YOLO
from picamera2 import Picamera2

from config import (
    MODEL_PATH, CONF_THRESHOLD, FRAME_WIDTH, FRAME_HEIGHT,
    CLASS_COLORS, CAMERA_FORMAT, CAMERA_SLEEP, IGNORE_CLASSES
)
from timestep_logger import TimeStepLogger
from show_activate import ShowActivate

class YOLOCameraDetector:
    def __init__(self):
        print("[INIT] Loading Model & System...")
        self.model = YOLO(MODEL_PATH)
        self.logger = TimeStepLogger()
        self.viewer = ShowActivate()

        self.frame_width = FRAME_WIDTH
        self.frame_height = FRAME_HEIGHT
        
        # List to store current boxes for mouse interaction
        self.current_boxes_ui = [] 
        
        print("[INIT] Starting Camera...")
        self.picam2 = Picamera2()
        cfg = self.picam2.create_still_configuration(
            main={"format": CAMERA_FORMAT, "size": (self.frame_width, self.frame_height)}
        )
        self.picam2.configure(cfg)
        self.picam2.start()
        time.sleep(CAMERA_SLEEP)
        print("[READY] System Started!")

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            if x < self.frame_width:
                for (x1, y1, x2, y2, cls_name) in self.current_boxes_ui:
                    if x1 <= x <= x2 and y1 <= y <= y2:
                        print(f"[CLICK] Selected: {cls_name}")
                        self.viewer.show_specific_item(cls_name)
                        return
            else:
                pass

        elif event == cv2.EVENT_RBUTTONDOWN:
            self.viewer.close_panel()
            print("[CLICK] Closed Panel")

    def run(self):
        window_name = "Smart Fridge System"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(window_name, self.mouse_callback)

        try:
            while True:
                frame = self.picam2.capture_array()
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                results = self.model.predict(frame, verbose=False, imgsz=self.frame_width)
                r = results[0]
                
                frame_boxes_temp = []

                boxes = r.boxes.xyxy.cpu().numpy()
                scores = r.boxes.conf.cpu().numpy()
                classes = r.boxes.cls.cpu().numpy()

                annotated_frame = frame.copy()

                for i in range(len(boxes)):
                    score = float(scores[i])
                    if score < CONF_THRESHOLD: continue
                    
                    cls = int(classes[i])
                    if cls in IGNORE_CLASSES:
                       continue
                    class_name = r.names[cls]
                    box = boxes[i]
                    x1, y1, x2, y2 = map(int, box)

                    # 1. Update Logger Tracking
                    self.logger.log_first_detect(cls, class_name, score)
                    
                    # --- [FIXED] THIS LINE WAS MISSING ---
                    # Check if time exceeded threshold to trigger activation
                    self.logger.check_and_log_activation(cls, class_name)
                    # -------------------------------------

                    # 2. Get Time Duration
                    duration = self.logger.get_duration(cls)
                    minutes = int(duration // 60)
                    seconds = int(duration % 60)
                    time_str = f"{minutes}m {seconds}s"

                    # 3. Get Status
                    is_active = self.logger.is_activated(cls)
                    is_stable = self.logger.logged_initial.get(cls, False)
                    
                    color = CLASS_COLORS.get(cls, (255, 255, 255))
                    
                    # Draw Box
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
                    
                    # Create Label
                    if is_stable:
                        status_txt = "ACTIVATED " if is_active else ""
                        label = f"{class_name} | {time_str} | {status_txt}"
                        frame_boxes_temp.append((x1, y1, x2, y2, class_name))
                    else:
                        label = f"{class_name} (checking...)"
                    
                    # Draw Label
                    (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                    cv2.rectangle(annotated_frame, (x1, y1 - 20), (x1 + w, y1), color, -1)
                    cv2.putText(annotated_frame, label, (x1, y1 - 5), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

                self.current_boxes_ui = frame_boxes_temp
                
                self.logger.check_active_timeouts()

                if self.viewer.is_visible:
                    panel = self.viewer.get_image()
                    if panel.shape[0] != annotated_frame.shape[0]:
                        panel = cv2.resize(panel, (int(panel.shape[1]), annotated_frame.shape[0]))
                    final_display = np.hstack((annotated_frame, panel))
                else:
                    final_display = annotated_frame

                cv2.imshow(window_name, final_display)

                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'): 
                    break
                if key == ord('s'):
                    if self.viewer.is_visible: 
                        self.viewer.close_panel()

        finally:
            self.picam2.stop()
            self.picam2.close()
            cv2.destroyAllWindows()
            print("[EXIT] Cleanup done.")

if __name__ == "__main__":
    app = YOLOCameraDetector()
    app.run()
