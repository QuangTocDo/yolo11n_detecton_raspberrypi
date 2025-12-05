import cv2
from picamera2 import Picamera2
from ultralytics import YOLO
import time
import numpy as np

model_path = "/home/rpi/project/yolo11n_2.pt"
model = YOLO(model_path)

CONF_THRESHOLD = 0.60

picam2 = Picamera2()
camera_config = picam2.create_still_configuration(
    main={"format": "RGB888", "size": (640, 480)}
)
picam2.configure(camera_config)
picam2.start()
time.sleep(1)

cv2.namedWindow("YOLOv11 Realtime", cv2.WINDOW_NORMAL)

FRAME_WIDTH = 640
FRAME_HEIGHT = 480

print("Press 'c' to capture & save detection, 'q' to quit.")

fps = 0
prev_time = time.time()

# --- MÀU CỐ ĐỊNH CHO 5 CLASS ---
CLASS_COLORS = {
    0: (0, 255, 0),    # xanh lá
    1: (0, 0, 255),    # đỏ
    2: (255, 0, 0),    # xanh dương
    3: (0, 255, 255),  # vàng
    4: (255, 0, 255),  # tím
    5: (255,255,0),
    6: (128,0,255),
    7: (255,128,0),
    8: (0,128,255),
    9: (128,255,0),
}


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

    return inter / union if union > 0 else 0


try:
    while True:
        frame = picam2.capture_array()
        frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        results = model.predict(frame_bgr, verbose=False, imgsz=FRAME_WIDTH)
        r = results[0]

        boxes = r.boxes.xyxy.cpu().numpy()
        scores = r.boxes.conf.cpu().numpy()
        classes = r.boxes.cls.cpu().numpy()

        filtered = []  # lưu box đã lọc

        # --- CUSTOM NMS + CONFIDENCE FILTER ---
        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes[i]
            score = float(scores[i])
            cls_id = int(classes[i])
            if cls_id in [3, 4]:
                continue
            # BỎ QUA nếu CONF < 60%
            if score < CONF_THRESHOLD:
                continue

            keep = True
            for fx1, fy1, fx2, fy2, fs, fc in filtered:
                iou = compute_iou((x1, y1, x2, y2), (fx1, fy1, fx2, fy2))

                # Nếu trùng > 0.55 → giữ box có CONF cao nhất
                if iou > 0.55:
                    if score <= fs:
                        keep = False
                    else:
                        filtered.remove((fx1, fy1, fx2, fy2, fs, fc))
                    break

            if keep:
                filtered.append((x1, y1, x2, y2, score, cls_id))

        # --- VẼ BOX THEO CLASS MÀU CỐ ĐỊNH ---
        annotated_frame = frame_bgr.copy()
        for x1, y1, x2, y2, score, cls_id in filtered:
            color = CLASS_COLORS.get(cls_id, (255, 255, 255))  # trắng nếu class ngoài 0-4
            cv2.rectangle(annotated_frame,
                          (int(x1), int(y1)), (int(x2), int(y2)),
                          color, 2)
            cv2.putText(annotated_frame,
                        f"{r.names[cls_id]} {score:.2f}",
                        (int(x1), int(y1) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, color, 2)

        # --- FPS ---
        curr_time = time.time()
        fps = 1 / (curr_time - prev_time)
        prev_time = curr_time
        cv2.putText(annotated_frame, f"FPS: {fps:.2f}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (0, 255, 255), 2)

        # Show frame
        cv2.imshow("YOLOv11 Realtime", annotated_frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("c"):
            filename = f"detection_{int(time.time())}.jpg"
            cv2.imwrite("/home/rpi/project/captures/" + filename, annotated_frame)
            print(f"Saved: {filename}")
        elif key == ord("q"):
            break

finally:
    cv2.destroyAllWindows()
    picam2.close()
