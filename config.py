# ============================
# CONFIG - CHỈ CẦN SỬA FILE NÀY
# ============================

# YOLO
MODEL_PATH = "/home/rpi/project/yolo11n_2.pt"
CONF_THRESHOLD = 0.6
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FRAME_W = 120
FRAME_H = 120
# LOGGER
ACTIVATE_MINUTES = 1  # sau bao nhiêu phút thì ACTIVATE
RESET_AFTER_SECONDS = 30
STABLE_FRAME_COUNT = 5
# CAMERA
CAMERA_FORMAT = "RGB888"  # hoặc RGB888, RGB888_3L, ...
CAMERA_SLEEP = 1          # delay sau khi start cam

# CUSTOM NMS
IOU_THRESHOLD = 0.55
IGNORE_CLASSES = [3, 4]  # bỏ qua class không quan trọng

# CLASS COLORS
CLASS_COLORS = {
    0: (0, 255, 0),
    1: (0, 0, 0),
    2: (255, 0, 0),
    3: (0, 255, 255),
    4: (255, 0, 255),
    5: (255, 255, 0),
    6: (128, 0, 255),
    7: (255, 128, 0),
    8: (0, 128, 255),
    9: (128, 255, 0),
}
