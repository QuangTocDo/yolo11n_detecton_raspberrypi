from ultralytics import YOLO
import cv2
import glob
import time
import os

def inference_score(folder_path, weights_path, device="cpu", conf_thresh=0.50, resize_to=None, save_folder="inference_output"):
    os.makedirs(save_folder, exist_ok=True)

    model = YOLO(weights_path)

    image_paths = glob.glob(folder_path + "/*.*")
    if len(image_paths) == 0:
        print("No images found!")
        return

    start_time = time.time()

    for img_path in image_paths:
        img = cv2.imread(img_path)
        img = cv2.cvtColor(img,cv2.COLOR_RGB2BGR)
        if img is None:
            continue

        if resize_to is not None:
            img = cv2.resize(img, resize_to)

        results = model(img, device=device, verbose=False, conf=conf_thresh)

        result_img = results[0].plot()
        filename = os.path.basename(img_path)
        save_path = os.path.join(save_folder, filename)
        cv2.imwrite(save_path, result_img)

    end_time = time.time()

    total_time = end_time - start_time
    num_imgs = len(image_paths)

    fps = num_imgs / total_time
    ms_per_image = (total_time / num_imgs) * 1000

    print("========== Inference Benchmark ==========")
    print(f"Total images: {num_imgs}")
    print(f"Total time: {total_time:.3f} sec")
    print(f"FPS: {fps:.2f}")
    print(f"ms per image: {ms_per_image:.2f} ms")
    print(f"Saved results to: {save_folder}")
    print("=========================================")

if __name__ == "__main__":
    inference_score(
        folder_path="/home/rpi/project/test",
        weights_path="/home/rpi/project/yolo11n_v2.pt",
        device="cpu",
        save_folder="/home/rpi/project/output"
    )
