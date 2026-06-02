# src/video_worker.py

import cv2
from PyQt5.QtCore import QThread, pyqtSignal, QMutex, QMutexLocker
from src.settings import AppSettings
from src.pipeline import process_frame


class VideoWorker(QThread):
    frame_ready = pyqtSignal(object)   # emits processed BGR frame (numpy array)
    status = pyqtSignal(str)

    def __init__(self, camera_index=0):
        """
        camera_index can be:
        - int (0, 1, 2...) for laptop webcam
        - str URL ("http://192.168.x.x:8080/video") for IP phone camera
        """
        super().__init__()
        self.camera_index = camera_index
        self._running = True

        self._mutex = QMutex()
        self._settings = AppSettings()  # local copy

    def update_settings(self, settings: AppSettings):
        # Make a safe snapshot copy (dataclass -> dict -> dataclass)
        with QMutexLocker(self._mutex):
            self._settings = AppSettings(**settings.__dict__)

    def stop(self):
        self._running = False

    def run(self):
        source = self.camera_index

        if isinstance(source, int):
            cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 15)
        else:
            cap = cv2.VideoCapture(source)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # critical for IP cam

        if not cap.isOpened():
            self.status.emit("Camera not found / cannot open.")
            return

        self.status.emit("Camera started.")

        while self._running:

            # DROP OLD FRAMES (low latency trick)
            cap.grab()
            ok, frame = cap.read()

            if not ok or frame is None:
                continue

            with QMutexLocker(self._mutex):
                s = self._settings

            out = process_frame(frame, s)

            self.frame_ready.emit(out)

        cap.release()
        self.status.emit("Camera stopped.")

