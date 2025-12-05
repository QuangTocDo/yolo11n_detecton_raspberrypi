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


class YOLOCameraDetector:
    def __init__(self):
        self.model = YOLO(MODEL_PATH)
        self.logger = TimeStepLogger()

        self.frame_width = FRAME_WIDTH
        self.frame_height = FRAME_HEIGHT
        self.conf_threshold = CONF_THRESHOLD

        self.colors = CLASS_COLORS
        self._init_camera()

    def _init_camera(self):
        self.picam2 = Picamera2()
        cfg = self.picam2.create_still_configuration(
            main={"format": CAMERA_FORMAT,
                  "size": (self.frame_width, self.frame_height)}
        )
        self.picam2.configure(cfg)
        self.picam2.start()
        time.sleep(CAMERA_SLEEP)

    @staticmethod
    def compute_iou(box1, box2):
        x1, y1, x2, y2 = box1
        X1, Y1, X2, Y2 = box2

        xx1 = max(x1, X1)
        yy1 = max(y1, Y1)
        xx2 = min(x2, X2)
        yy2 = min(y2, Y2)

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

            if cls in IGNORE_CLASSES:
                continue
            if score < self.conf_threshold:
                continue

            keep = True
            for fx1, fy1, fx2, fy2, fs, fc in filtered:
                iou = self.compute_iou((x1, y1, x2, y2),
                                       (fx1, fy1, fx2, fy2))
                if iou > IOU_THRESHOLD:
                    if score <= fs:
                        keep = False
                    else:
                        filtered.remove((fx1, fy1, fx2, fy2, fs, fc))
                    break

            if keep:
                filtered.append((x1, y1, x2, y2, score, cls))
        return filtered

    def draw_boxes(self, frame, filtered, names):
        out = frame.copy()
        for x1, y1, x2, y2, score, cls in filtered:
            color = self.colors.get(cls, (255, 255, 255))
            duration = self.logger.get_duration(cls)
            text = f"{names[cls]} {score:.2f} "

            cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            cv2.putText(out, text, (int(x1), int(y1)-5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return out

    def run(self):
        cv2.namedWindow("YOLO Realtime", cv2.WINDOW_NORMAL)
        prev = time.time()

        while True:
            frame = self.picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            results = self.model.predict(frame, verbose=False, imgsz=self.frame_width)
            r = results[0]

            boxes = r.boxes.xyxy.cpu().numpy()
            scores = r.boxes.conf.cpu().numpy()
            classes = r.boxes.cls.cpu().numpy()

            filtered = self.custom_filter(boxes, scores, classes)

            # LOGGER + ACTIVATE
            for x1, y1, x2, y2, score, cls in filtered:
                name = r.names[cls]
                self.logger.log_first_detect(cls, name, score)
                self.logger.check_and_log_activation(cls, name)

            annotated = self.draw_boxes(frame, filtered, r.names)

            # FPS
            now = time.time()
            fps = 1 / (now - prev)
            prev = now
            cv2.putText(annotated, f"FPS: {fps:.2f}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1,
                        (0, 255, 255), 2)

            cv2.imshow("YOLO Realtime", annotated)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

        cv2.destroyAllWindows()
        self.picam2.close()
