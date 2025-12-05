import cv2
import os
import glob
import numpy as np
import json
from PIL import Image, ImageDraw, ImageFont # Cần cài: pip install pillow
from config import FRAME_WIDTH, FRAME_HEIGHT

class ShowActivate:
    def __init__(self):
        self.log_dir = "logs"
        self.data_dir = "data"
        self.json_path = os.path.join(self.data_dir, "data.json")
        
        self.is_visible = False 
        self.current_index = 0
        self.items = []
        self.db = {} 
        
        # --- CẤU HÌNH ĐƯỜNG DẪN FONT ---
        # Đây là đường dẫn font có sẵn trên Raspberry Pi của bạn
        self.font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        
        # Kiểm tra xem font có thực sự ở đó không
        if not os.path.exists(self.font_path):
            print(f"[WARNING] Khong tim thay font tai: {self.font_path}")
            # Nếu không tìm thấy, code sẽ tự dùng font mặc định (xấu hơn)
        else:
            print(f"[OK] Da tim thay font he thong: {self.font_path}")

        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        
        # Load dữ liệu lần đầu
        self.refresh_database()

    def refresh_database(self):
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, 'r', encoding='utf-8') as f:
                    self.db = json.load(f)
                print(f"[DEBUG] Da load DB: {len(self.db)} san pham.")
            except Exception as e:
                print(f"[ERROR] Loi doc JSON: {e}")
                self.db = {}
        else:
            self.db = {}

    def _get_activated_classes(self):
        activated_classes = set()
        pattern = os.path.join(self.log_dir, "*_activated.csv")
        files = glob.glob(pattern)
        for filepath in files:
            filename = os.path.basename(filepath)
            class_name = filename.replace("_activated.csv", "")
            activated_classes.add(class_name)
        return sorted(list(activated_classes))

    def refresh_list(self):
        self.items = self._get_activated_classes()
        self.refresh_database()
        if self.current_index >= len(self.items):
            self.current_index = 0

    def navigate(self, direction):
        total = len(self.items)
        if total == 0: return
        self.current_index += direction
        if self.current_index >= total:
            self.current_index = 0
        elif self.current_index < 0:
            self.current_index = total - 1

    # --- HÀM VẼ TEXT BẰNG PIL (HỖ TRỢ TIẾNG VIỆT) ---
    def draw_text_pil(self, draw, text, pos, font_size, color):
        try:
            font = ImageFont.truetype(self.font_path, font_size)
        except IOError:
            font = ImageFont.load_default()
            
        draw.text(pos, text, font=font, fill=color)
        
        bbox = draw.textbbox(pos, text, font=font)
        text_height = bbox[3] - bbox[1]
        return text_height + 10 

    def draw_wrapped_text_pil(self, draw, text, x, y, max_width, font_size, color):
        try:
            font = ImageFont.truetype(self.font_path, font_size)
        except:
            font = ImageFont.load_default()

        words = text.split(' ')
        current_line = ""
        line_height = font_size + 10 
        
        for word in words:
            test_line = current_line + word + " "
            bbox = draw.textbbox((0, 0), test_line, font=font)
            w = bbox[2] - bbox[0]
            
            if w > max_width:
                draw.text((x, y), current_line, font=font, fill=color)
                y += line_height
                current_line = word + " "
            else:
                current_line = test_line
        
        if current_line:
            draw.text((x, y), current_line, font=font, fill=color)
            y += line_height
            
        return y

    def get_image(self):
        # 1. Tạo nền đen OpenCV
        canvas = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
        
        # 2. Chuyển sang PIL để vẽ chữ đẹp
        img_pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        
        total_items = len(self.items)

        if total_items == 0:
            self.draw_text_pil(draw, "CHƯA CÓ SẢN PHẨM NÀO!", (30, FRAME_HEIGHT//2), 25, (255, 0, 0))
            self.draw_text_pil(draw, "(Hãy kiểm tra thư mục logs/)", (30, FRAME_HEIGHT//2 + 40), 18, (200, 200, 200))
        else:
            key_name = self.items[self.current_index]
            info = self.db.get(key_name, None)

            if info is None:
                self.draw_text_pil(draw, f"Thiếu thông tin trong data.json:", (30, FRAME_HEIGHT//2 - 20), 20, (255, 165, 0))
                self.draw_text_pil(draw, key_name, (30, FRAME_HEIGHT//2 + 20), 30, (255, 255, 255))
            else:
                margin_left = 30
                y = 40
                max_w = FRAME_WIDTH - 50

                # Header Index
                self.draw_text_pil(draw, f"Sản phẩm {self.current_index + 1}/{total_items}", (FRAME_WIDTH - 180, 20), 16, (0, 255, 255))

                # Tên sản phẩm
                y = self.draw_wrapped_text_pil(draw, info.get('name', key_name).upper(), margin_left, y, max_w, 30, (0, 255, 0))
                
                # Kẻ đường ngang
                draw.line([(margin_left, y), (FRAME_WIDTH - 30, y)], fill=(100, 100, 100), width=2)
                y += 20

                # Xuất xứ & Độ cồn
                origin = info.get('origin', 'N/A')
                abv = info.get('abv', 'N/A')
                sub_header = f"Xuất xứ: {origin}  |  Độ cồn: {abv}"
                y = self.draw_wrapped_text_pil(draw, sub_header, margin_left, y, max_w, 18, (0, 200, 255))
                y += 10

                # Giống nho
                grape = info.get('grape', 'N/A')
                y = self.draw_wrapped_text_pil(draw, f"Giống nho: {grape}", margin_left, y, max_w, 18, (200, 200, 200))
                y += 20

                # Hương vị
                self.draw_text_pil(draw, "[ Hương vị ]", (margin_left, y), 20, (150, 150, 255))
                y += 25
                y = self.draw_wrapped_text_pil(draw, info.get('taste', ''), margin_left + 15, y, max_w, 16, (230, 230, 230))
                y += 10

                # Món ăn kèm
                self.draw_text_pil(draw, "[ Kết hợp món ăn ]", (margin_left, y), 20, (150, 150, 255))
                y += 25
                y = self.draw_wrapped_text_pil(draw, info.get('pair', ''), margin_left + 15, y, max_w, 16, (230, 230, 230))

        # Footer
        footer_y = FRAME_HEIGHT - 30
        draw.line([(0, footer_y - 10), (FRAME_WIDTH, footer_y - 10)], fill=(50, 50, 50), width=1)
        self.draw_text_pil(draw, "[n]: Tiếp  |  [p]: Trước  |  [x]: Đóng", (30, footer_y), 14, (0, 165, 255))

        # 3. Chuyển ngược về OpenCV để hiển thị
        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
