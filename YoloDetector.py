import cv2
import time
import numpy as np
from ultralytics import YOLO
from picamera2 import Picamera2

from config import (
    MODEL_PATH, CONF_THRESHOLD, FRAME_WIDTH, FRAME_HEIGHT,
    CLASS_COLORS, CAMERA_FORMAT, CAMERA_SLEEP,
    IOU_THRESHOLD, IGNORE_CLASSES
)
from timestep_logger import TimeStepLogger
from show_activate import ShowActivate

class YOLOCameraDetector:
    def __init__(self):
        print("Loading Model...")
        self.model = YOLO(MODEL_PATH)
        self.logger = TimeStepLogger()
        self.viewer = ShowActivate()

        self.frame_width = FRAME_WIDTH
        self.frame_height = FRAME_HEIGHT
        self.conf_threshold = CONF_THRESHOLD
        self.colors = CLASS_COLORS
        
        print("Initializing Camera...")
        self._init_camera()

    def _init_camera(self):
        self.picam2 = Picamera2()
        cfg = self.picam2.create_still_configuration(
            main={"format": CAMERA_FORMAT, "size": (self.frame_width, self.frame_height)}
        )
        self.picam2.configure(cfg)
        self.picam2.start()
        time.sleep(CAMERA_SLEEP)
        print("Camera Ready!")

    @staticmethod
    def compute_iou(box1, box2):
        x1, y1, x2, y2 = box1
        X1, Y1, X2, Y2 = box2
        xx1 = max(x1, X1); yy1 = max(y1, Y1)
        xx2 = min(x2, X2); yy2 = min(y2, Y2)
        inter = max(0, xx2 - xx1) * max(0, yy2 - yy1)
        area1 = (x2 - x1) * (y2 - y1)
        area2 = (X2 - X1) * (Y2 - Y1)
        union = area1 + area2 - inter
        return inter / union if union else 0

    def custom_filter(self, boxes, scores, classes):
        filtered = []
        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes[i]
            score = float(scores[i])
            cls = int(classes[i])
            if cls in IGNORE_CLASSES: continue
            if score < self.conf_threshold: continue
            keep = True
            for fx1, fy1, fx2, fy2, fs, fc in filtered:
                iou = self.compute_iou((x1, y1, x2, y2), (fx1, fy1, fx2, fy2))
                if iou > IOU_THRESHOLD:
                    if score <= fs: keep = False
                    else: filtered.remove((fx1, fy1, fx2, fy2, fs, fc))
                    break
            if keep: filtered.append((x1, y1, x2, y2, score, cls))
        return filtered

    def draw_boxes(self, frame, filtered, names):
        out = frame.copy()
        for x1, y1, x2, y2, score, cls in filtered:
            is_active = self.logger.is_activated(cls)
            duration = self.logger.get_duration(cls)
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            time_str = f"{minutes}m {seconds}s"
            
            if is_active:
                label = f"{names[cls]} | {time_str} ACTIVATED"
            else:
                label = f"{names[cls]} | {time_str}"

            color = self.colors.get(cls, (255, 255, 255))
            
            cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(out, (int(x1), int(y1) - 25), (int(x1) + w, int(y1)), color, -1)
            cv2.putText(out, label, (int(x1), int(y1) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        return out

    def run(self):
        window_name = "YOLO System"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        
        prev = time.time()
        print("He thong bat dau.")
        print("Phim tat: [S] Mo Info | [X] Tat Info | [Q] Thoat")

        while True:
            # 1. Capture & Process
            frame = self.picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            results = self.model.predict(frame, verbose=False, imgsz=self.frame_width)
            r = results[0]
            boxes = r.boxes.xyxy.cpu().numpy()
            scores = r.boxes.conf.cpu().numpy()
            classes = r.boxes.cls.cpu().numpy()

            filtered = self.custom_filter(boxes, scores, classes)

            for x1, y1, x2, y2, score, cls in filtered:
                 name = r.names[cls]
                 self.logger.log_first_detect(cls, name, score)
                 self.logger.check_and_log_activation(cls, name)

            # Hình ảnh Camera sau khi vẽ
            annotated_frame = self.draw_boxes(frame, filtered, r.names)

            # Tính FPS
            now = time.time()
            fps = 1 / (now - prev) if (now - prev) > 0 else 0
            prev = now
            cv2.putText(annotated_frame, f"FPS: {fps:.1f}", (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

            # --- HIỂN THỊ HƯỚNG DẪN TRÊN MÀN HÌNH ---
            help_text = "[S]: Info ON/OFF  |  [X]: Close Info  |  [Q]: Quit"
            cv2.putText(annotated_frame, help_text, (10, self.frame_height - 15), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            # -----------------------------------------------------
            # LOGIC GỘP MÀN HÌNH (STITCHING)
            # -----------------------------------------------------
            if self.viewer.is_visible:
                # Lấy hình ảnh Info
                info_frame = self.viewer.get_image()
                
                # Resize cho khớp chiều cao nếu cần
                if info_frame.shape[0] != annotated_frame.shape[0]:
                    info_frame = cv2.resize(info_frame, (int(info_frame.shape[1]), annotated_frame.shape[0]))

                # GỘP 2 HÌNH (Trái: Camera, Phải: Info)
                final_display = np.hstack((annotated_frame, info_frame))
            else:
                # Chỉ hiện Camera
                final_display = annotated_frame
            # -----------------------------------------------------
            self. logger.check_active_timeouts()
            # Hiển thị
            cv2.imshow(window_name, final_display)

            # Xử lý phím
            key = cv2.waitKey(1) & 0xFF
            
            if key == ord("q"):
                break
            
            # --- CÁC PHÍM ĐIỀU KHIỂN ---
            if key == ord("s"): # Bật/Tắt (Toggle)
                self.viewer.is_visible = not self.viewer.is_visible
                if self.viewer.is_visible:
                    self.viewer.refresh_list()
                    print("-> Info Panel: ON")
                else:
                    print("-> Info Panel: OFF")
            
            if key == ord("x"): # Tắt Info (Force Close)
                self.viewer.is_visible = False
                print("-> Info Panel: OFF")

            # Phím điều hướng slide (chỉ tác dụng khi đang hiện info)
            if self.viewer.is_visible:
                if key == ord("n"):
                    self.viewer.navigate(1)  # Next
                elif key == ord("p"):
                    self.viewer.navigate(-1) # Previous

        cv2.destroyAllWindows()
        self.picam2.close()

if __name__ == "__main__":
    detector = YOLOCameraDetector()
    detector.run()
