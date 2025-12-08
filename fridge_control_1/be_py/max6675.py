import spidev
import time

class MAX6675:
    def __init__(self, bus=1, device=0, max_speed_hz=5000000):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)  # Bus và CE (Chip Enable)
        self.spi.max_speed_hz = max_speed_hz

    def read_temperature(self):
        raw = self.spi.readbytes(2)  # Đọc 2 byte từ MAX6675
        value = (raw[0] << 8) | raw[1]

        if value & 0x4:  # Bit lỗi (không có thermocouple)
            return None

        value >>= 3  # Bỏ 3 bit cuối
        return value * 0.25  # Mỗi bước = 0.25°C

    def close(self):
        self.spi.close()
