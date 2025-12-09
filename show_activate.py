import cv2
import os
import glob
import numpy as np
import json
from PIL import Image, ImageDraw, ImageFont
from config import FRAME_WIDTH, FRAME_HEIGHT

class ShowActivate:
    def __init__(self):
        self.log_dir = "logs"
        self.data_dir = "data"
        self.json_path = os.path.join(self.data_dir, "data.json")
        
        self.is_visible = False 
        self.current_key = None 
        self.db = {} 
        
        self.font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not os.path.exists(self.font_path):
            self.font_path = "arial.ttf" 

        os.makedirs(self.data_dir, exist_ok=True)
        self.refresh_database()

    def refresh_database(self):
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, 'r', encoding='utf-8') as f:
                    self.db = json.load(f)
            except Exception:
                self.db = {}
        else:
            self.db = {}

    def show_specific_item(self, class_name):
        """Hiển thị thông tin class này ngay lập tức"""
        self.current_key = class_name
        self.is_visible = True 

    def close_panel(self):
        self.is_visible = False
        self.current_key = None

    def draw_text_pil(self, draw, text, pos, font_size, color):
        try:
            font = ImageFont.truetype(self.font_path, font_size)
        except IOError:
            font = ImageFont.load_default()
        draw.text(pos, text, font=font, fill=color)
        bbox = draw.textbbox(pos, text, font=font)
        return (bbox[3] - bbox[1]) + 10 

    def draw_wrapped_text_pil(self, draw, text, x, y, max_width, font_size, color):
        try:
            font = ImageFont.truetype(self.font_path, font_size)
        except:
            font = ImageFont.load_default()
        if not text: return y

        words = text.split(' ')
        current_line = ""
        bbox_sample = draw.textbbox((0, 0), "Wg", font=font)
        line_height = (bbox_sample[3] - bbox_sample[1]) + 8
        
        for word in words:
            test_line = current_line + word + " "
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if (bbox[2] - bbox[0]) > max_width:
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
        # Tạo nền đen
        canvas = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
        img_pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        
        # Nếu chưa chọn món nào
        if not self.current_key:
            self.draw_text_pil(draw, "CHỌN SẢN PHẨM", (30, FRAME_HEIGHT//2), 25, (255, 255, 255))
            self.draw_text_pil(draw, "(Click vào ô vuông trên màn hình)", (30, FRAME_HEIGHT//2 + 40), 18, (200, 200, 200))
        else:
            # Lấy thông tin từ DB
            key_name = self.current_key
            info = self.db.get(key_name, None)

            if info is None:
                self.draw_text_pil(draw, "Không tìm thấy dữ liệu:", (30, 100), 20, (255, 100, 100))
                self.draw_text_pil(draw, key_name, (30, 140), 30, (255, 255, 255))
            else:
                # --- VẼ GIAO DIỆN ---
                margin = 30
                y = 40
                w = FRAME_WIDTH - 50
                
                # Tên sản phẩm
                y = self.draw_wrapped_text_pil(draw, info.get('name', key_name).upper(), margin, y, w, 30, (0, 255, 0))
                draw.line([(margin, y), (FRAME_WIDTH - 30, y)], fill=(100, 100, 100), width=2)
                y += 20

                # Chi tiết cơ bản (Origin, ABV)
                sub = f"Xuất xứ: {info.get('origin','N/A')} | ABV: {info.get('abv','N/A')}"
                y = self.draw_wrapped_text_pil(draw, sub, margin, y, w, 18, (0, 200, 255))
                y += 10
                
                # Dòng Nho
                y = self.draw_wrapped_text_pil(draw, f"Nho: {info.get('grape','N/A')}", margin, y, w, 18, (200, 200, 200))
                y += 10

                # --- [MỚI] Dòng Target Temp ---
                # Lấy dữ liệu target_temp từ JSON, nếu không có thì hiện N/A
                temp_val = info.get('target_temp', 'N/A')
                y = self.draw_wrapped_text_pil(draw, f"Nhiệt độ dùng: {temp_val}", margin, y, w, 18, (255, 215, 0)) # Màu vàng Gold
                y += 20
                
                # Hương vị
                self.draw_text_pil(draw, "[ Hương vị ]", (margin, y), 20, (150, 150, 255))
                y += 25
                y = self.draw_wrapped_text_pil(draw, info.get('taste', ''), margin + 15, y, w, 16, (230, 230, 230))
                y += 10
                
                # Kết hợp
                self.draw_text_pil(draw, "[ Kết hợp ]", (margin, y), 20, (150, 150, 255))
                y += 25
                y = self.draw_wrapped_text_pil(draw, info.get('pair', ''), margin + 15, y, w, 16, (230, 230, 230))

        # Footer
        footer_y = FRAME_HEIGHT - 30
        draw.line([(0, footer_y - 10), (FRAME_WIDTH, footer_y - 10)], fill=(50, 50, 50), width=1)
        self.draw_text_pil(draw, "[Click]: Chọn món khác | [Chuột phải/S]: Đóng", (20, footer_y), 14, (0, 165, 255))

        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
