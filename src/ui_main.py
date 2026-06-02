import os
import cv2
import numpy as np
import json
import base64
import hashlib
import secrets
import pyttsx3
import speech_recognition as sr
from PyQt5.QtCore import QThread, pyqtSignal
from datetime import datetime

# PyQt5 Widgets
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QCheckBox, QSlider,
    QVBoxLayout, QHBoxLayout, QMessageBox, QGroupBox,
    QStackedWidget, QFrame, QSizePolicy, QGraphicsOpacityEffect,
    QLineEdit, QTextEdit, QFileDialog, QScrollArea,
    QFormLayout, QDialog, QDialogButtonBox, QInputDialog
)

# PyQt5 Core
from PyQt5.QtCore import (
    Qt, QTimer, QPoint,
    QPropertyAnimation, QEasingCurve,
    QSequentialAnimationGroup, QParallelAnimationGroup, QRect
)

# PyQt5 GUI
from PyQt5.QtGui import QImage, QPixmap

# Project files
from src.video_worker import VideoWorker
from src.settings import AppSettings, save_profile, load_profile
from src.logger import log_event

PROFILE_DIR = "data/profiles"

USERS_DIR = "data/users"
USERS_FILE = os.path.join(USERS_DIR, "users.json")


def hline():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    line.setStyleSheet("color:#d1d5db;")
    return line


def big_title(text: str):
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size: 34px; font-weight: 950; color: #0b1220;")
    return lbl


def small_desc(text: str):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("font-size: 16px; color: #334155;")
    return lbl


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def load_users() -> dict:
    _ensure_dir(USERS_DIR)
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"users": {}}, f, indent=2)
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_users(data: dict):
    _ensure_dir(USERS_DIR)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def hash_pin(pin: str, salt_b64: str = None) -> dict:
    """
    PBKDF2 hash for PIN. Stores salt + hash in base64.
    """
    if salt_b64 is None:
        salt = secrets.token_bytes(16)
        salt_b64 = base64.b64encode(salt).decode("utf-8")
    else:
        salt = base64.b64decode(salt_b64.encode("utf-8"))

    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 100_000)
    hash_b64 = base64.b64encode(dk).decode("utf-8")
    return {"salt": salt_b64, "hash": hash_b64}


def verify_pin(pin: str, salt_b64: str, hash_b64: str) -> bool:
    test = hash_pin(pin, salt_b64=salt_b64)["hash"]
    return secrets.compare_digest(test, hash_b64)


def user_profile_dir(username: str) -> str:
    return os.path.join(PROFILE_DIR, username)

def guest_profile_dir() -> str:
    return os.path.join(PROFILE_DIR, "Guest")


def _load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

class VoiceWorker(QThread):
    command_detected = pyqtSignal(str)

    def run(self):
        r = sr.Recognizer()

        try:
            with sr.Microphone() as source:
                print("Listening...")
                audio = r.listen(source, timeout=5)

            cmd = r.recognize_google(audio)
            print("You said:", cmd)

            self.command_detected.emit(cmd)

        except Exception as e:
            print("Voice error:", e)
            self.command_detected.emit("")        

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.voice_engine = pyttsx3.init()
        self.voice_lines = []
        self.voice_index = 0
        self.voice_timer = QTimer()
        self.voice_timer.timeout.connect(self.read_next_line)
        self.is_paused = False

        self.setWindowTitle("LightSensePro")
        self.setMinimumSize(1280, 760)

        self.settings = AppSettings()

        self.worker = VideoWorker()
        self.worker.frame_ready.connect(self.on_frame)
        self.worker.status.connect(self.on_status)
        self.worker_started = False
        # track which camera source is active (0 = webcam, str = IP url)
        self._camera_source = 0

        self.current_user = None  # {"username": "..."}

        # Keep references for animations/timers
        self._anim_refs = []
        self._dots_timer = None
        self._splash_anim = None

        # Keep references for camera toggle buttons (Stop/Turn On)
        self._camera_toggle_buttons = []
        # Keep references for user badges (circle + name) so both Home and Normal update together
        self._user_badges = []

        # Root navigation stack (Login -> Welcome -> Splash -> Post-Welcome -> App)
        self.root_stack = QStackedWidget()
        self.setCentralWidget(self.root_stack)

        self.login_page = self.build_login_page()
        self.welcome_page = self.build_welcome_page()
        self.splash_page = self.build_splash_page()
        self.post_welcome_page = self.build_post_welcome_page()
        self.app_page = self.build_app_page()
        self.page_voice = self.build_page_voice_full()
        self.center_stack.addWidget(self.page_voice)
        self.page_health = self.build_page_health()
        self.center_stack.addWidget(self.page_health)
        self.page_medicine_tracker = self.build_page_medicine_tracker()
        self.center_stack.addWidget(self.page_medicine_tracker)
        self.root_stack.addWidget(self.login_page)         # 0
        self.root_stack.addWidget(self.welcome_page)       # 1
        self.root_stack.addWidget(self.splash_page)        # 2
        self.root_stack.addWidget(self.post_welcome_page)  # 3
        self.root_stack.addWidget(self.app_page)           # 4
        self.root_stack.setCurrentWidget(self.login_page)
        
                # ---------------- Peripheral / Wide Field state ----------------
        self.peripheral_on = False
        self.widefield_on = False
        self.widefield_mode = 1 

        self._prev_gray = None
        self._last_motion_ts = 0
        self._motion_cooldown_ms = 900  # debounce alerts
        self._motion_text = ""  # "Motion: Left/Right"
        self._motion_text_ts = 0

        self.apply_theme()

    def safe_speak(self, text):
        try:
            self.voice_engine.stop()
            self.voice_engine.say(text)
            self.voice_engine.runAndWait()
        except:
            pass    
        
    def start_listening(self):
        self.safe_speak("Listening")

        self.voice_thread = VoiceWorker()
        self.voice_thread.command_detected.connect(self.on_voice_result)
        self.voice_thread.start()


    def on_voice_result(self, cmd):
        if not cmd:
            self.safe_speak("Sorry, I did not understand")
            return

        self.safe_speak("You said " + cmd)
        self.handle_voice_command(cmd)

    def handle_voice_command(self, command: str):
        cmd = command.lower()

        if "reading" in cmd:
            self.ensure_worker_started()
            self.switch_page(2)

        elif "comfort" in cmd or "profile" in cmd:
            self.switch_page(6)

        elif "voice" in cmd:
            self.center_stack.setCurrentWidget(self.page_voice)

        else:
            self.safe_speak("Command not recognized")  

    # ---------------- THEME (WHITE APP) ----------------

    def apply_theme(self):
        self.setStyleSheet("""
            QMainWindow { background: #ffffff; }
            QWidget { background: #ffffff; color: #0b1220; font-size: 18px; }

            QGroupBox {
                border: none;
                background: transparent;
            }
            QFrame {
                border: none;
                background: transparent;
            }              
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 10px 0 10px;
                color: #0b1220;
                font-weight: 900;
                font-size: 18px;
            }

            QLabel { background: transparent; }

            QPushButton {
                border: 1px solid #e5e7eb;
                border-radius: 16px;
                padding: 14px 16px;
                background: #f8fafc;
                color: #0b1220;
                font-weight: 900;
                font-size: 18px;
            }
            QPushButton:hover { background: #eef2ff; }
            QPushButton:pressed { background: #e5e7eb; }

            QPushButton#primary {
                background: #2563eb;
                border: 1px solid #2563eb;
                color: white;
            }
            QPushButton#primary:hover { background: #1d4ed8; }

            QPushButton#danger {
                background: #b91c1c;
                border: 1px solid #b91c1c;
                color: white;
                font-size: 18px;
                font-weight: 950;
                padding: 16px 16px;
            }
            QPushButton#danger:hover { background: #991b1b; }

            QCheckBox { spacing: 12px; font-size: 18px; font-weight: 800; color:#0b1220; }
            QCheckBox::indicator {
                width: 22px; height: 22px;
                border-radius: 7px;
                border: 1px solid #cbd5e1;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #22c55e;
                border: 1px solid #22c55e;
            }

            QSlider::groove:horizontal {
                border: 2px solid #cbd5e1;
                height: 12px;
                border-radius: 6px;
                background: #e2e8f0;
            }
            QSlider::handle:horizontal {
                background: #2563eb;
                border: 2px solid #2563eb;
                width: 30px;
                margin: -12px 0;
                border-radius: 15px;
            }

        """)

    # ---------------- LOGIN (FULLSCREEN like Welcome/Splash) ----------------
    # FIXED: Fullscreen card + login_status exists + Enter triggers login

    def build_login_page(self):
        page = QWidget()
        page.setStyleSheet("QWidget { background: #eaf2ff; }")

        root = QVBoxLayout(page)
        root.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        root.setContentsMargins(60, 60, 60, 60)
        root.setSpacing(0)

        card = QFrame()
        card.setMinimumHeight(560)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border: 0px;
                border-radius: 34px;
            }
        """)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        inner = QFrame()
        inner.setMaximumWidth(560)
        inner.setStyleSheet("QFrame { background: transparent; }")

        v = QVBoxLayout(inner)
        v.setContentsMargins(34, 30, 34, 28)
        v.setSpacing(14)

        title = QLabel("Login")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 44px; font-weight: 1000; color:#0b1220;")

        accent = QFrame()
        accent.setFixedHeight(6)
        accent.setMaximumWidth(140)
        accent.setStyleSheet("background:#22c55e; border-radius:3px;")

        accent_wrap = QHBoxLayout()
        accent_wrap.setContentsMargins(0, 0, 0, 0)
        accent_wrap.addStretch(1)
        accent_wrap.addWidget(accent)
        accent_wrap.addStretch(1)

        self.login_user = QLineEdit()
        self.login_user.setPlaceholderText("Username")
        self.login_user.setMinimumHeight(52)
        self.login_user.setStyleSheet("""
            QLineEdit {
                border: 1px solid #e5e7eb;
                border-radius: 16px;
                padding: 12px 14px;
                background: #f8fafc;
                font-size: 18px;
            }
            QLineEdit:focus {
                border: 1px solid #22c55e;
                background: #ffffff;
            }
        """)

        self.login_pin = QLineEdit()
        self.login_pin.setPlaceholderText("PIN")
        self.login_pin.setEchoMode(QLineEdit.Password)
        self.login_pin.setMinimumHeight(52)
        self.login_pin.setStyleSheet(self.login_user.styleSheet())

        # ✅ FIX: login_status label exists (previously missing -> crash on Enter/login)
        self.login_status = QLabel("")
        self.login_status.setAlignment(Qt.AlignCenter)
        self.login_status.setStyleSheet("color:#b91c1c; font-weight:900; font-size:16px;")
        self.login_status.setFixedHeight(28)

        btn_login = QPushButton("Login")
        btn_login.setMinimumHeight(58)
        btn_login.setStyleSheet("""
            QPushButton {
                background: #22c55e;
                border: none;
                border-radius: 18px;
                font-size: 20px;
                font-weight: 900;
                color: white;
                padding: 14px;
            }
            QPushButton:hover { background: #16a34a; }
            QPushButton:pressed { background: #15803d; }
        """)
        btn_login.clicked.connect(self.handle_login)

        btn_create = QPushButton("Create Account")
        btn_create.setMinimumHeight(56)
        btn_create.clicked.connect(self.open_create_account_dialog)

        # ✅ Enter triggers login (no app close)
        self.login_user.returnPressed.connect(btn_login.click)
        self.login_pin.returnPressed.connect(btn_login.click)

        v.addStretch(1)
        v.addWidget(title)
        v.addLayout(accent_wrap)
        v.addSpacing(8)
        v.addWidget(self.login_user)
        v.addWidget(self.login_pin)
        v.addWidget(self.login_status)
        v.addSpacing(6)
        v.addWidget(btn_login)
        v.addWidget(btn_create)
        v.addStretch(1)

        mid = QVBoxLayout()
        mid.addStretch(1)
        mid.addWidget(inner, 0, Qt.AlignHCenter)
        mid.addStretch(1)

        card_layout.addLayout(mid)

        root.addWidget(card, 1)
        return page

    # ---------------- CREATE ACCOUNT DIALOG ----------------

    def open_create_account_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Create Account")
        dlg.setMinimumWidth(420)

        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        user = QLineEdit()
        user.setPlaceholderText("e.g. iflah")
        pin1 = QLineEdit()
        pin1.setPlaceholderText("PIN (4-8 digits)")
        pin1.setEchoMode(QLineEdit.Password)
        pin2 = QLineEdit()
        pin2.setPlaceholderText("Confirm PIN")
        pin2.setEchoMode(QLineEdit.Password)

        form.addRow("Username", user)
        form.addRow("PIN", pin1)
        form.addRow("Confirm PIN", pin2)

        status = QLabel("")
        status.setStyleSheet("color:#b91c1c; font-weight:800;")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(lambda: self._create_account_submit(dlg, user, pin1, pin2, status))
        buttons.rejected.connect(dlg.reject)

        lay.addLayout(form)
        lay.addWidget(status)
        lay.addWidget(buttons)

        dlg.exec_()

    def _create_account_submit(self, dlg, user, pin1, pin2, status_label):
        username = user.text().strip()
        p1 = pin1.text().strip()
        p2 = pin2.text().strip()

        if not username:
            status_label.setText("Username required.")
            return
        if " " in username:
            status_label.setText("Username must not contain spaces.")
            return
        if len(p1) < 4 or len(p1) > 8 or not p1.isdigit():
            status_label.setText("PIN must be 4-8 digits.")
            return
        if p1 != p2:
            status_label.setText("PINs do not match.")
            return

        data = load_users()
        users = data.get("users", {})

        if username in users:
            status_label.setText("Username already exists.")
            return

        hp = hash_pin(p1)
        users[username] = {
            "salt": hp["salt"],
            "hash": hp["hash"],
            "created_at": datetime.now().isoformat(timespec="seconds")
        }
        data["users"] = users
        save_users(data)

        os.makedirs(user_profile_dir(username), exist_ok=True)

        self.current_user = {"username": username}
        self.update_user_badge()
        self.refresh_medicine_cards()
        if hasattr(self, "login_status"):
            self.login_status.setText("")
        dlg.accept()

        self.root_stack.setCurrentWidget(self.welcome_page)
        QTimer.singleShot(0, self.animate_welcome_card)

    def handle_login(self):
        username = self.login_user.text().strip()
        pin = self.login_pin.text().strip()

        if not username or not pin:
            self.login_status.setText("Enter username and PIN.")
            return

        data = load_users()
        users = data.get("users", {})
        if username not in users:
            self.login_status.setText("User not found. Create an account.")
            return

        rec = users[username]
        if not verify_pin(pin, rec["salt"], rec["hash"]):
            self.login_status.setText("Wrong PIN.")
            return

        self.current_user = {"username": username}
        os.makedirs(user_profile_dir(username), exist_ok=True)

        self.login_status.setText("")
        self.update_user_badge()

        self.refresh_medicine_cards()
        self.root_stack.setCurrentWidget(self.welcome_page)
        QTimer.singleShot(0, self.animate_welcome_card)

    # ---------------- CAMERA HELPERS ----------------

    def start_camera(self):
        """
        Start camera safely.
        IMPORTANT: After stopping a QThread, it cannot be started again.
        So we recreate VideoWorker when needed.
        """
        if self.worker_started:
            return

        try:
            if self.worker is None or (hasattr(self.worker, "isRunning") and not self.worker.isRunning()):
                self.worker = VideoWorker(camera_index=self._camera_source)
                self.worker.frame_ready.connect(self.on_frame)
                self.worker.status.connect(self.on_status)
        except Exception:
            self.worker = VideoWorker(camera_index=self._camera_source)
            self.worker.frame_ready.connect(self.on_frame)
            self.worker.status.connect(self.on_status)

        try:
            self.worker.start()
            self.worker_started = True
            self._set_camera_buttons_running(True)

            try:
                self.worker.update_settings(self.settings)
            except Exception:
                pass
        except Exception:
            self.worker_started = False
            self._set_camera_buttons_running(False)

    def ensure_worker_started(self):
        if not self.worker_started:
            self.start_camera()

    def _register_camera_button(self, btn: QPushButton):
        if btn not in self._camera_toggle_buttons:
            self._camera_toggle_buttons.append(btn)
        self._set_camera_buttons_running(self.worker_started)

    def _set_camera_buttons_running(self, running: bool):
        for b in getattr(self, "_camera_toggle_buttons", []):
            if b is None:
                continue
            b.setText("Stop Camera" if running else "Turn Camera On")

    def stop_camera_preview(self):
        try:
            if hasattr(self, "worker") and self.worker:
                self.worker.stop()
                try:
                    self.worker.wait(500)
                except Exception:
                    pass
            self.worker_started = False
        except Exception:
            pass

        self._set_camera_buttons_running(False)

        if hasattr(self, "video_label"):
            self.video_label.clear()
            self.video_label.setText("Camera Preview")
        if hasattr(self, "status_label"):
            self.status_label.setText("Status: Stopped")

        if hasattr(self, "_home_mini_preview"):
            self._home_mini_preview.clear()
            self._home_mini_preview.setText("Preview stopped")

    def toggle_camera(self):
        if self.worker_started:
            self.stop_camera_preview()
        else:
            self.start_camera()
            if hasattr(self, "status_label"):
                self.status_label.setText("Status: Ready")

    # ---------------- USER BADGE (GREEN + SYNC) ----------------

    def _register_user_badge(self, name_lbl: QLabel, circle_lbl: QLabel):
        self._user_badges.append((name_lbl, circle_lbl))
        self.update_user_badge()

    # ---------------- ANIMATIONS ----------------

    def animate_popup(self, widget: QWidget, delay_ms=0, dy=24, dur=260):
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
        eff.setOpacity(0.0)

        end_pos = widget.pos()
        start_pos = end_pos + QPoint(0, dy)
        widget.move(start_pos)

        anim_op = QPropertyAnimation(eff, b"opacity", self)
        anim_op.setDuration(dur)
        anim_op.setStartValue(0.0)
        anim_op.setEndValue(1.0)
        anim_op.setEasingCurve(QEasingCurve.OutCubic)

        anim_pos = QPropertyAnimation(widget, b"pos", self)
        anim_pos.setDuration(dur)
        anim_pos.setStartValue(start_pos)
        anim_pos.setEndValue(end_pos)
        anim_pos.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim_op)
        group.addAnimation(anim_pos)

        def start_group():
            group.start()
            self._anim_refs.append(group)

        if delay_ms > 0:
            QTimer.singleShot(delay_ms, start_group)
        else:
            start_group()

    def restart_camera(self, source):
        # remember current source
        self._camera_source = source

        # stop old worker if running
        try:
            if getattr(self, "worker_started", False):
                self.worker.stop()
                self.worker.wait(1000)
        except Exception:
            pass

        # start new worker
        self.worker = VideoWorker(camera_index=source)
        self.worker.frame_ready.connect(self.on_frame)
        self.worker.status.connect(self.on_status)

        try:
            self.worker.start()
            self.worker_started = True
            self._set_camera_buttons_running(True)
            try:
                self.worker.update_settings(self.settings)
            except Exception:
                pass
        except Exception:
            self.worker_started = False
            self._set_camera_buttons_running(False)

    # ---------------- POST WELCOME (FULLSCREEN) ----------------

    def build_post_welcome_page(self):
        page = QWidget()
        page.setStyleSheet("QWidget { background: #eaf2ff; }")

        lay = QVBoxLayout(page)
        lay.setContentsMargins(60, 60, 60, 60)

        card = QFrame()
        card.setMinimumHeight(560)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border: 0px;
                border-radius: 34px;
            }
        """)

        v = QVBoxLayout(card)
        v.setContentsMargins(80, 70, 80, 70)
        v.setSpacing(18)

        title = QLabel("WELCOME")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 64px; font-weight: 1000; color:#0b1220;")

        sub = QLabel("Home is ready — choose a mode to begin.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("font-size: 22px; font-weight: 800; color:#334155;")

        v.addStretch(1)
        v.addWidget(title)
        v.addWidget(sub)
        v.addStretch(1)

        lay.addWidget(card, 1)

        self._post_welcome_card = card
        QTimer.singleShot(0, self.animate_post_welcome_card)

        return page

    def animate_post_welcome_card(self):
        if not hasattr(self, "_post_welcome_card"):
            return
        card = self._post_welcome_card

        eff = QGraphicsOpacityEffect(card)
        card.setGraphicsEffect(eff)
        eff.setOpacity(0.0)

        start_pos = card.pos() + QPoint(0, 30)
        end_pos = card.pos()
        card.move(start_pos)

        anim_op = QPropertyAnimation(eff, b"opacity", self)
        anim_op.setDuration(280)
        anim_op.setStartValue(0.0)
        anim_op.setEndValue(1.0)
        anim_op.setEasingCurve(QEasingCurve.OutCubic)

        anim_pos = QPropertyAnimation(card, b"pos", self)
        anim_pos.setDuration(280)
        anim_pos.setStartValue(start_pos)
        anim_pos.setEndValue(end_pos)
        anim_pos.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim_op)
        group.addAnimation(anim_pos)
        group.start()
        self._anim_refs.append(group)

    # ---------------- WELCOME (FULLSCREEN) ----------------

    def build_welcome_page(self):
        page = QWidget()
        page.setStyleSheet("QWidget { background: #ffffff; }")

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        overlay = QFrame()
        overlay.setStyleSheet("QFrame { background: rgba(0,0,0,0.08); }")
        overlay.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        overlay_layout = QVBoxLayout(overlay)
        overlay_layout.setContentsMargins(60, 60, 60, 60)
        overlay_layout.setSpacing(20)

        card = QFrame()
        card.setMinimumHeight(620)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setStyleSheet("""
            QFrame {
                background: #f6f9ff;
                border: 0px;
                border-radius: 34px;
            }
        """)

        outer = QVBoxLayout(card)
        outer.setContentsMargins(70, 60, 70, 60)
        outer.setSpacing(22)

        intro_stack = QStackedWidget()
        intro_stack.setStyleSheet("QStackedWidget { background: transparent; }")

        # Step 1
        step1 = QWidget()
        s1 = QVBoxLayout(step1)
        s1.setContentsMargins(0, 0, 0, 0)
        s1.setSpacing(16)

        title1 = QLabel("LightSensePro")
        title1.setAlignment(Qt.AlignCenter)
        title1.setStyleSheet("font-size: 72px; font-weight: 1000; color:#0b1220;")
        next_btn = QPushButton("Next  →")
        next_btn.setMinimumHeight(72)

        next_btn.setStyleSheet("""
            QPushButton {
                background: #2563eb;
                border: 1px solid #2563eb;
                color: white;
                font-size: 22px;
                font-weight: 1000;
                border-radius: 20px;
                padding: 16px 18px;
            }

            QPushButton:hover {
                background: #1d4ed8;
            }

            QPushButton:pressed {
                background: #1e40af;
            }
        """)

        next_btn.clicked.connect(
            lambda: intro_stack.setCurrentIndex(1)
        )
        s1.addWidget(title1)
        s1.addStretch(1)
        s1.addWidget(next_btn)

        # Step 2
        step2 = QWidget()
        s2 = QVBoxLayout(step2)
        s2.setContentsMargins(0, 0, 0, 0)
        s2.setSpacing(20)

        title2 = QLabel("LightSensePro")
        title2.setAlignment(Qt.AlignCenter)
        title2.setStyleSheet("font-size: 68px; font-weight: 1000; color:#0b1220;")

        start_btn = QPushButton("Start App")
        start_btn.setMinimumHeight(90)
        start_btn.setStyleSheet("""
            QPushButton {
                background: #2563eb;
                border: 1px solid #2563eb;
                color: white;
                font-size: 24px;
                font-weight: 1000;
                border-radius: 26px;
                padding: 20px 18px;
            }
            QPushButton:hover { background: #1d4ed8; }
            QPushButton:pressed { background: #1e40af; }
        """)
        start_btn.clicked.connect(self.enter_app)

        enter_hint = QLabel("Press Enter to continue")
        enter_hint.setAlignment(Qt.AlignCenter)
        enter_hint.setStyleSheet("font-size: 16px; color:#475569; font-weight: 700;")

        s2.addStretch(1)
        s2.addWidget(title2)
        s2.addSpacing(10)
        s2.addWidget(start_btn)
        s2.addWidget(enter_hint)
        s2.addStretch(1)

        intro_stack.addWidget(step1)
        intro_stack.addWidget(step2)
        intro_stack.setCurrentIndex(0)

        self._welcome_intro_stack = intro_stack
        self._welcome_start_btn = start_btn

        outer.addWidget(intro_stack)
        overlay_layout.addWidget(card, 1)
        layout.addWidget(overlay)

        self._welcome_card = card
        QTimer.singleShot(0, self.animate_welcome_card)

        return page

    def animate_welcome_card(self):
        if not hasattr(self, "_welcome_card"):
            return
        card = self._welcome_card

        eff = QGraphicsOpacityEffect(card)
        card.setGraphicsEffect(eff)
        eff.setOpacity(0.0)

        start_pos = card.pos() + QPoint(0, 20)
        end_pos = card.pos()
        card.move(start_pos)

        anim_op = QPropertyAnimation(eff, b"opacity", self)
        anim_op.setDuration(260)
        anim_op.setStartValue(0.0)
        anim_op.setEndValue(1.0)
        anim_op.setEasingCurve(QEasingCurve.OutCubic)

        anim_pos = QPropertyAnimation(card, b"pos", self)
        anim_pos.setDuration(260)
        anim_pos.setStartValue(start_pos)
        anim_pos.setEndValue(end_pos)
        anim_pos.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim_op)
        group.addAnimation(anim_pos)
        group.start()
        self._anim_refs.append(group)

    # ---------------- SPLASH (FULLSCREEN) ----------------

    def build_splash_page(self):
        page = QWidget()
        page.setStyleSheet("QWidget { background: #eaf2ff; }")

        lay = QVBoxLayout(page)
        lay.setContentsMargins(60, 60, 60, 60)

        card = QFrame()
        card.setMinimumHeight(560)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border: 0px;
                border-radius: 34px;
            }
        """)

        v = QVBoxLayout(card)
        v.setContentsMargins(80, 70, 80, 70)
        v.setSpacing(18)

        title = QLabel("LightSensePro")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 64px; font-weight: 1000; color:#0b1220;")

        self._dots = QLabel("...")
        self._dots.setAlignment(Qt.AlignCenter)
        self._dots.setStyleSheet("font-size: 40px; font-weight: 1000; color:#2563eb;")

        self._line1 = QLabel("Loading Visual Comfort")
        self._line2 = QLabel("Loading Reading Assist")
        self._line3 = QLabel("Loading Emergency Mode")

        for lbl in [self._line1, self._line2, self._line3]:
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("font-size: 24px; font-weight: 900; color:#1f2a44;")

        self._line_eff1 = QGraphicsOpacityEffect(self._line1)
        self._line_eff2 = QGraphicsOpacityEffect(self._line2)
        self._line_eff3 = QGraphicsOpacityEffect(self._line3)
        self._line1.setGraphicsEffect(self._line_eff1)
        self._line2.setGraphicsEffect(self._line_eff2)
        self._line3.setGraphicsEffect(self._line_eff3)
        self._line_eff1.setOpacity(0.0)
        self._line_eff2.setOpacity(0.0)
        self._line_eff3.setOpacity(0.0)

        v.addStretch(1)
        v.addWidget(title)
        v.addWidget(self._dots)
        v.addSpacing(12)
        v.addWidget(self._line1)
        v.addWidget(self._line2)
        v.addWidget(self._line3)
        v.addStretch(1)

        lay.addWidget(card, 1)
        return page

    def start_splash_animation(self):
        if self._dots_timer is None:
            self._dots_timer = QTimer(self)
            self._dots_timer.setInterval(240)

            def tick():
                t = self._dots.text()
                if t == ".":
                    self._dots.setText("..")
                elif t == "..":
                    self._dots.setText("...")
                else:
                    self._dots.setText(".")

            self._dots_timer.timeout.connect(tick)

        self._dots.setText(".")
        self._dots_timer.start()

        self._line_eff1.setOpacity(0.0)
        self._line_eff2.setOpacity(0.0)
        self._line_eff3.setOpacity(0.0)

        a1 = QPropertyAnimation(self._line_eff1, b"opacity", self)
        a1.setDuration(220)
        a1.setStartValue(0.0)
        a1.setEndValue(1.0)

        a2 = QPropertyAnimation(self._line_eff2, b"opacity", self)
        a2.setDuration(220)
        a2.setStartValue(0.0)
        a2.setEndValue(1.0)

        a3 = QPropertyAnimation(self._line_eff3, b"opacity", self)
        a3.setDuration(220)
        a3.setStartValue(0.0)
        a3.setEndValue(1.0)

        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(a1)
        seq.addPause(90)
        seq.addAnimation(a2)
        seq.addPause(90)
        seq.addAnimation(a3)
        seq.start()

        self._splash_anim = seq
        self._anim_refs.append(seq)

    # ---------------- APP PAGE (SIDEBAR + HOME + NORMAL) ----------------

    def build_app_page(self):
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        self.sidebar = self.build_sidebar()
        self.sidebar.setVisible(False)
        self.sidebar.setFixedWidth(0)

        self.video_label = QLabel("Camera Preview")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background:#0b1220; border: 1px solid #e5e7eb; border-radius: 22px; color:#e5e7eb; "
            "font-size: 19px; font-weight: 800;"
        )
        self.video_label.setMinimumSize(800, 600)
        self.video_label.setMaximumSize(1400, 900)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.status_label = QLabel("Status: Ready")
        self.status_label.setStyleSheet("color:#334155; font-size: 16px; font-weight: 800;")

        self.page_stack = QStackedWidget()
        self.page_stack.setFixedWidth(450)
        self.page_home = self.build_page_home()
        self.page_visual = self.build_page_visual_modes()
        self.page_reading = self.build_page_reading()
        self.page_profiles = self.build_page_profiles()
        self.page_about = self.build_page_about()
        self.page_rp = self.build_page_rp_profile()
        self.page_injections = self.build_page_injections_history()
        self.page_peripheral = self.build_page_peripheral_wide()
        self.page_stack.addWidget(self.page_home)     # 0
        self.page_stack.addWidget(self.page_visual)   # 1
        self.page_stack.addWidget(self.page_reading)  # 2
        self.page_stack.addWidget(self.page_profiles) # 3
        self.page_stack.addWidget(self.page_about)    # 4
        self.page_stack.addWidget(self.page_peripheral)  # 5
        self.page_stack.addWidget(self.page_rp)          # 6
        self.page_stack.addWidget(self.page_injections)  # 7


        normal = QWidget()
        normal_root = QHBoxLayout(normal)
        normal_root.setContentsMargins(0, 0, 0, 0)
        normal_root.setSpacing(14)

        left_col = QVBoxLayout()
        left_col.setSpacing(10)

        top_bar = QHBoxLayout()
        menu_btn = QPushButton("☰")
        menu_btn.setFixedSize(44, 44)
        menu_btn.clicked.connect(self.toggle_sidebar)
        mic_btn = QPushButton("🎤")
        mic_btn.setFixedSize(66, 66)
        mic_btn.setStyleSheet("""
            QPushButton {
                font-size: 30px;              /* icon size */
                border-radius: 33px;          /* perfect circle */
                background-color: #2563eb;    /* blue */
                color: white;
                border: none;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        mic_btn.clicked.connect(self.start_listening)
        top_bar.addWidget(menu_btn)
        top_bar.addStretch()   # 👈 pushes mic to right
        top_bar.addWidget(mic_btn)

        badge = self.build_user_badge()
        top_bar.addWidget(badge, alignment=Qt.AlignLeft)

        top_bar.addStretch(1)
        left_col.addLayout(top_bar)

        preview_col = QVBoxLayout()
        preview_col.setSpacing(10)
        preview_col.addWidget(self.video_label, 1)
        preview_col.addWidget(self.status_label, 0)

        left_col.addLayout(preview_col, 1)

        normal_root.addLayout(left_col, 1)
        normal_root.addWidget(self.page_stack, 0)

        self.home_full = self.build_page_home()
        self.home_full.setStyleSheet("QWidget { background: #ffffff; color: #0b1220; }")
        self.rp_full = self.build_page_rp_profile()
        self.rp_full.setStyleSheet("QWidget { background: #ffffff; color: #0b1220; }")

        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.home_full)  # 0
        self.center_stack.addWidget(normal)          # 1 (camera layout)
        self.center_stack.addWidget(self.rp_full)    # 2 (full-screen, no camera)
        self.center_stack.setCurrentIndex(0)


        root.addWidget(self.sidebar, 0)
        root.addWidget(self.center_stack, 1)

        return page
    
    def build_page_rp_profile(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(20)

        # ---------------- TITLE ----------------
        title = QLabel("RP Comfort Profile")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            font-size: 42px;
            font-weight: 1000;
            color: #0b1220;
        """)
        root.addWidget(title)

        # ---------- INTRO PAGE ----------
        intro = QWidget()
        iv = QVBoxLayout(intro)
        self.loading_label = QLabel("⟳")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet("""
            font-size: 50px;
            color: #2563eb;
        """)

        iv.addWidget(self.loading_label)
        self._swirl_states = ["⟳", "⟲", "⟳", "⟲"]

        intro_label = QLabel("Let's optimize your vision comfort...")
        intro_label.setAlignment(Qt.AlignCenter)
        intro_label.setStyleSheet("""
            font-size: 26px;
            font-weight: 1000;
            color: #2563eb;
        """)

        iv.addStretch(1)
        iv.addWidget(intro_label)
        iv.addStretch(1)

        self.rp_intro_label = intro_label

        # ---------------- STACK (QUESTIONS) ----------------
        self.rp_stack = QStackedWidget()
        self.rp_answers = {}

        # ---------- QUESTION BUILDER ----------
        def create_question(question_text, key, options):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setSpacing(18)

            # blue card
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: #eef4ff;
                    border-radius: 22px;
                }
            """)

            cv = QVBoxLayout(card)
            cv.setContentsMargins(30, 30, 30, 30)
            cv.setSpacing(18)

            # question
            q = QLabel(question_text)
            q.setWordWrap(True)
            q.setAlignment(Qt.AlignCenter)
            q.setStyleSheet("""
                font-size: 24px;
                font-weight: 1000;
                color: #0b1220;
            """)
            cv.addWidget(q)

            # options
            for text, value in options:
                btn = QPushButton(text)
                btn.setMinimumHeight(60)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                btn.setStyleSheet("""
                    QPushButton {
                        background: #2563eb;
                        color: white;
                        border-radius: 16px;
                        font-size: 18px;
                        font-weight: 900;
                    }
                    QPushButton:hover {
                        background: #1d4ed8;
                    }
                """)
                btn.clicked.connect(lambda _, k=key, v=value: self.handle_rp_answer(k, v))
                cv.addWidget(btn)

            # 🔙 BACK BUTTON
            back_btn = QPushButton("← Back")
            back_btn.setMinimumHeight(45)
            back_btn.setStyleSheet("""
                QPushButton {
                    background: #e2e8f0;
                    border-radius: 12px;
                    font-size: 16px;
                    font-weight: 800;
                }
                QPushButton:hover {
                    background: #cbd5f5;
                }
            """)
            back_btn.clicked.connect(self.rp_go_back)

            cv.addWidget(back_btn)

            v.addStretch(1)
            v.addWidget(card)
            v.addStretch(1)

            return w

        # ---------- QUESTIONS ----------
        q1 = create_question(
            "How often do you feel eye strain while using screens?",
            "strain",
            [("Daily", "daily"), ("Occasionally", "occasionally"), ("Rarely", "rarely")]
        )

        q2 = create_question(
            "Do you struggle to read small text clearly?",
            "reading",
            [("Yes, often", "high"), ("Sometimes", "medium"), ("No", "low")]
        )

        q3 = create_question(
            "Are you sensitive to bright lights or glare?",
            "glare",
            [("Yes, very sensitive", "high"), ("Slightly", "medium"), ("No", "low")]
        )

        q4 = create_question(
            "Do you find it difficult to see in low light or at night?",
            "night",
            [("Yes", "yes"), ("Sometimes", "sometimes"), ("No", "no")]
        )

        q5 = create_question(
            "Do you miss objects or movement from the sides?",
            "peripheral",
            [("Yes, frequently", "high"), ("Sometimes", "medium"), ("No", "low")]
        )

        q6 = create_question(
            "Do your eyes get tired quickly while reading or working?",
            "fatigue",
            [("Yes", "yes"), ("Sometimes", "sometimes"), ("No", "no")]
        )

        # ---------- DONE PAGE ----------
        done = QWidget()
        dv = QVBoxLayout(done)
                # -------- RESULT CARD --------
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: white;
                border-radius: 18px;
                padding: 25px;
            }
        """)

        card_layout = QVBoxLayout(card)
        card_layout.setAlignment(Qt.AlignCenter)

        # TITLE
        self.rp_result_title = QLabel("👁️ Your visual comfort")
        self.rp_result_title.setAlignment(Qt.AlignCenter)
        self.rp_result_title.setStyleSheet("""
            font-size: 34px;
            font-weight: 900;
            color: #0b1220;
            margin-bottom: 10px;
        """)

        # DESCRIPTION
        self.rp_result_desc = QLabel("")
        self.rp_result_desc.setAlignment(Qt.AlignCenter)
        self.rp_result_desc.setWordWrap(True)
        self.rp_result_desc.setStyleSheet("""
            font-size: 30px;
            color: #0b1220;
            margin-top: 30px;
            margin-bottom: 40px;
            font-weight: 400;

        """)

        # BUTTON
        again_btn = QPushButton("Retake Assessment")
        again_btn.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                border-radius: 20px;
                padding: 16px;
                font-size: 20px;
                font-weight: 700;
                min-width: 260px;
            }
        """)
        again_btn.clicked.connect(self.start_rp_intro)

        # ADD TO CARD
        card_layout.addWidget(self.rp_result_title)
        card_layout.addWidget(self.rp_result_desc)
        card_layout.addWidget(again_btn)

        # ADD TO PAGE
        dv.addStretch(1)
        dv.addWidget(card)
        dv.addStretch(1)

        # add to stack
        for w in [intro, q1, q2, q3, q4, q5, q6, done]:
            self.rp_stack.addWidget(w)

        root.addWidget(self.rp_stack)

        return page
    def start_rp_intro(self):
        self.rp_stack.setCurrentIndex(0)

        # start swirl animation
        self.swirl_timer = QTimer()
        self.swirl_timer.timeout.connect(self.update_swirl)
        self.swirl_timer.start(120)

        # stop animation + go to Q1
        def go_next():
            self.swirl_timer.stop()
            self.rp_stack.setCurrentIndex(1)

        QTimer.singleShot(1500, go_next)

    def update_swirl(self):
        current = self.loading_label.text()
        idx = self._swirl_states.index(current) if current in self._swirl_states else 0
        self.loading_label.setText(self._swirl_states[(idx + 1) % len(self._swirl_states)])
        
    def rp_go_back(self):
        current = self.rp_stack.currentIndex()
        if current > 0:
            self.rp_stack.setCurrentIndex(current - 1)
        
    def build_page_voice_full(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # ---------- TOP BAR ----------
        top_row = QHBoxLayout()

        menu_btn = QPushButton("☰")
        menu_btn.setFixedSize(44, 44)
        menu_btn.clicked.connect(self.toggle_sidebar)

        badge = self.build_user_badge()

        top_row.addWidget(menu_btn)
        top_row.addWidget(badge)
        top_row.addStretch(1)

        root.addLayout(top_row)

        # ---------- TITLE ----------
        title = QLabel("Voice Assist")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 40px; font-weight: 1000;")
        root.addWidget(title)

        root.addWidget(hline())

        # ---------- MAIN LAYOUT ----------
        main = QHBoxLayout()
        main.setSpacing(16)

        # ===== LEFT → DOCUMENT VIEW =====
        self.voice_text = QTextEdit()
        self.voice_text.setReadOnly(True)
        self.voice_text.setStyleSheet("""
            QTextEdit {
                background: #f8fafc;
                border-radius: 18px;
                padding: 20px;
                font-size: 40px;
            }
        """)

        main.addWidget(self.voice_text, 3)  # BIG area

        # ===== RIGHT → CONTROL PANEL =====
        panel = QFrame()
        panel.setFixedWidth(280)
        panel.setStyleSheet("""
            QFrame {
                background: #f1f5f9;
                border-radius: 18px;
            }
        """)

        pv = QVBoxLayout(panel)
        pv.setContentsMargins(14, 14, 14, 14)
        pv.setSpacing(12)

        btn_open = QPushButton("Open Document")
        btn_open.clicked.connect(self.open_voice_file)
        btn_play = QPushButton("Play")
        btn_pause = QPushButton("Pause")
        btn_stop = QPushButton("Stop")
        btn_restart = QPushButton("Restart")
        btn_play.clicked.connect(self.start_voice)
        btn_pause.clicked.connect(self.pause_voice)
        btn_stop.clicked.connect(self.stop_voice)
        btn_restart.clicked.connect(self.restart_voice)

        pv.addWidget(btn_open)
        pv.addWidget(btn_play)
        pv.addWidget(btn_pause)
        pv.addWidget(btn_stop)
        pv.addWidget(btn_restart)
        pv.addStretch(1)

        main.addWidget(panel, 1)

        root.addLayout(main, 1)

        return page
    
    def build_page_health(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(20)

        # ---------- TOP BAR ----------
        top_row = QHBoxLayout()

        menu_btn = QPushButton("☰")
        menu_btn.setFixedSize(44, 44)
        menu_btn.clicked.connect(self.toggle_sidebar)

        badge = self.build_user_badge()

        top_row.addWidget(menu_btn)
        top_row.addWidget(badge)
        top_row.addStretch(1)

        root.addLayout(top_row)

        # ---------- TITLE ----------
        title = QLabel("Health Mode")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            font-size: 42px;
            font-weight: 1000;
            color: #0b1220;
        """)
        root.addWidget(title)

        subtitle = QLabel("Track medicines, reminders, symptoms and eye care.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("""
            font-size: 18px;
            color: #475569;
            font-weight: 700;
        """)
        root.addWidget(subtitle)

        root.addWidget(hline())

        # ---------- CARD BUILDER ----------
        def health_card(title_text):
            card = QFrame()
            card.setMinimumSize(1500, 750)

            card.setStyleSheet("""
                QFrame {
                    background: #eaf2ff;
                    border-radius: 28px;
                }
            """)

            lay = QVBoxLayout(card)
            lay.setContentsMargins(40, 40, 40, 40)
            lay.setSpacing(12)

            title = QLabel(title_text)
            title.setAlignment(Qt.AlignCenter)
            title.setStyleSheet("""
                font-size: 34px;
                font-weight: 700;
                color: #0b132b;
                background: transparent;
            """)
            btn = QPushButton("Open")
            if title_text == "Medicine Tracker":
                btn.clicked.connect(
                    lambda: self.center_stack.setCurrentWidget(
                        self.page_medicine_tracker
                    )
                )
            btn.setMinimumHeight(55)
            btn.setStyleSheet("""
                QPushButton {
                    background: #2563eb;
                    color: white;
                    border-radius: 14px;
                    font-size: 16px;
                    font-weight: 900;
                }

                QPushButton:hover {
                    background: #1d4ed8;
                }
            """)
            lay.addStretch()

            lay.addWidget(title, 0, Qt.AlignCenter)

            lay.addSpacing(120)

            lay.addWidget(btn, 0, Qt.AlignCenter)

            lay.addStretch()
            return card
        grid = QVBoxLayout()
        grid.setAlignment(Qt.AlignCenter)
        row1 = QHBoxLayout()
        row1.setSpacing(20)
        row1.addStretch()
        row1.addStretch()
        card = health_card("Medicine Tracker")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        row1.addWidget(card)
        row1.addStretch()
        grid.addLayout(row1)

        root.addLayout(grid)

        root.addStretch(1)

        return page
    
    def get_medicine_file(self):

        username = "Guest"

        if self.current_user and "username" in self.current_user:
            username = self.current_user["username"]

        folder = f"data/users/{username}"

        os.makedirs(folder, exist_ok=True)

        return os.path.join(folder, "medicines.json")

    def save_medicines(self, medicines):
        path = self.get_medicine_file()

        with open(path, "w") as f:
            json.dump(medicines, f, indent=4)


    def load_medicines(self):
        path = self.get_medicine_file()

        if not os.path.exists(path):
            return []

        with open(path, "r") as f:
            return json.load(f)
        
    def build_page_medicine_tracker(self):
        page = QWidget()

        root = QVBoxLayout(page)
        root.setContentsMargins(20,20,20,20)
        root.setSpacing(20)
        root.setAlignment(Qt.AlignTop)

        # ---------- TOP ----------
        top = QHBoxLayout()

        back_btn = QPushButton("Back")
        back_btn.setFixedHeight(42)

        back_btn.clicked.connect(
            lambda: self.center_stack.setCurrentWidget(self.page_health)
        )

        top.addWidget(back_btn)
        top.addStretch(1)

        root.addLayout(top)

        # ---------- TITLE ----------
        title = QLabel("Medicine Tracker")
        title.setAlignment(Qt.AlignCenter)

        title.setStyleSheet("""
            font-size:42px;
            font-weight:1000;
            color:#0b1220;
        """)

        subtitle = QLabel("Track medicines and daily intake.")
        subtitle.setAlignment(Qt.AlignCenter)

        subtitle.setStyleSheet("""
            font-size:18px;
            color:#475569;
            font-weight:700;
        """)

        root.addWidget(title)
        root.addWidget(subtitle)

        # ---------- MEDICINE AREA ----------
        self.medicine_container = QVBoxLayout()
        self.medicine_container.setSpacing(20)

        root.addLayout(self.medicine_container)

        # ---------- ADD BUTTON ----------
        add_btn = QPushButton("Add Medicine")
        add_btn.setMinimumHeight(50)

        add_btn.setStyleSheet("""
            QPushButton {
                background:#2563eb;
                color:white;
                border-radius:14px;
                font-size:18px;
                font-weight:900;
            }

            QPushButton:hover {
                background:#1d4ed8;
            }
        """)

        add_btn.clicked.connect(self.add_medicine_card)

        root.addWidget(add_btn)

        root.addStretch(1)
        self.refresh_medicine_cards()

        return page
    
    def add_medicine_card(self):

        # Medicine name
        med_name, ok1 = QInputDialog.getText(
            self,
            "Medicine Name",
            "Enter medicine name:"
        )

        if not ok1 or not med_name:
            return

        # Time
        med_time, ok2 = QInputDialog.getText(
            self,
            "Medicine Time",
            "Enter time (e.g. 8:00 PM):"
        )

        if not ok2 or not med_time:
            return
        
        medicines = self.load_medicines()

        medicines.append({
            "name": med_name,
            "time": med_time,
            "taken": False
        })

        self.save_medicines(medicines)

        self.refresh_medicine_cards()

    def refresh_medicine_cards(self):

        # purane cards remove
        while self.medicine_container.count():

            item = self.medicine_container.takeAt(0)

            widget = item.widget()

            if widget:
                widget.deleteLater()

        medicines = self.load_medicines()

        for index, med in enumerate(medicines):

            card = QFrame()

            card.setFixedSize(700, 260)

            card.setStyleSheet("""
                QFrame {
                    background:#eef4ff;
                    border-radius:35px;
                }
            """)

            lay = QVBoxLayout(card)

            lay.setContentsMargins(35,30,35,25)
            lay.setSpacing(16)

            title = QLabel(med["name"])

            title.setAlignment(Qt.AlignCenter)

            title.setStyleSheet("""
                font-size:28px;
                font-weight:1000;
                color:#2563eb;
            """)

            time_lbl = QLabel(f'Time: {med["time"]}')

            time_lbl.setAlignment(Qt.AlignCenter)

            time_lbl.setStyleSheet("""
                font-size:18px;
                font-weight:800;
                color:#475569;
            """)

            taken_row = QHBoxLayout()

            taken_text = QLabel("Taken")

            taken_text.setStyleSheet("""
                font-size:22px;
                font-weight:900;
                color:#0b1220;
            """)

            taken_box = QCheckBox()

            taken_box.setChecked(med.get("taken", False))

            taken_box.setStyleSheet("""
                QCheckBox::indicator{
                    width:28px;
                    height:28px;
                }
            """)

            taken_row.addWidget(taken_text)
            taken_row.addStretch()
            taken_row.addWidget(taken_box)

            delete_btn = QPushButton("Delete")
            delete_btn.clicked.connect(
                lambda _, i=index: self.delete_medicine(i)
            )

            delete_btn.setFixedWidth(120)

            delete_btn.setStyleSheet("""
                QPushButton{
                    background:#ef4444;
                    color:white;
                    border-radius:12px;
                    font-weight:900;
                }

                QPushButton:hover{
                    background:#dc2626;
                }
            """)

            delete_row = QHBoxLayout()
            delete_row.addStretch()
            delete_row.addWidget(delete_btn)

            lay.addWidget(title)
            lay.addWidget(time_lbl)

            lay.addSpacing(15)

            lay.addLayout(taken_row)

            lay.addStretch(1)

            lay.addLayout(delete_row)

            self.medicine_container.addWidget(card)    
    def delete_medicine(self, index):

        medicines = self.load_medicines()

        if 0 <= index < len(medicines):
            medicines.pop(index)

        self.save_medicines(medicines)

        self.refresh_medicine_cards()        

    def handle_rp_answer(self, key, value):
        self.rp_answers[key] = value

        current = self.rp_stack.currentIndex()

        # move next
        if current < self.rp_stack.count() - 1:
            self.rp_stack.setCurrentIndex(current + 1)

        # last page (analysis)
        if self.rp_stack.currentIndex() == self.rp_stack.count() - 1:
            QTimer.singleShot(1200, self.generate_rp_result)
    
    def generate_rp_result(self):
        a = self.rp_answers

        suggestions = []

        # simple smart logic
        if a.get("reading") in ["high", "medium"]:
            suggestions.append("• Use Reading Mode for better text clarity")

        if a.get("peripheral") in ["high", "medium"]:
            suggestions.append("• Enable Peripheral Alert for side awareness")

        if a.get("glare") == "high":
            suggestions.append("• Avoid strong lights and screen glare")

        if a.get("night") == "yes":
            suggestions.append("• Use proper lighting in low-light environments")

        if a.get("fatigue") in ["yes", "sometimes"]:
            suggestions.append("• Take short breaks during screen use")

        if not suggestions:
            suggestions.append("• Your visual comfort is stable")

        final_text = "Your Comfort Profile:\n\n" + "\n".join(suggestions)
        self.rp_result_desc.setText(final_text)
    def open_voice_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "Text Files (*.txt)")

        if not file_path:
            return

        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        self.voice_text.setPlainText(text)

        self.voice_lines = text.split("\n")
        self.voice_index = 0

    def start_voice(self):
        if not self.voice_lines:
            return

        self.is_paused = False
        self.voice_timer.start(100)

    def read_next_line(self):
        if self.is_paused:
            return

        if self.voice_index >= len(self.voice_lines):
            self.voice_timer.stop()
            return

        line = self.voice_lines[self.voice_index].strip()

        if not line:
            self.voice_index += 1
            return

        # highlight
        cursor = self.voice_text.textCursor()
        cursor.movePosition(cursor.Start)

        for i in range(self.voice_index):
            cursor.movePosition(cursor.Down)

        cursor.select(cursor.LineUnderCursor)
        self.voice_text.setTextCursor(cursor)

               # speak
        self.voice_engine.say(line)
        self.voice_engine.runAndWait()

        self.voice_index += 1
    def pause_voice(self):
        self.is_paused = True
    def stop_voice(self):
        self.voice_timer.stop()
        self.voice_index = 0        
    def restart_voice(self):
        self.voice_index = 0
        self.is_paused = False
        self.voice_timer.start(100)
    # ---------- Chatbot logic ----------

    def _rp_choose(self, key: str, value: str):
        self._rp_answers[key] = value

        idx = self.rp_stack.currentIndex()
        if idx < self.rp_stack.count() - 1:
            self.rp_stack.setCurrentIndex(idx + 1)

        # If last page, save
        if self.rp_stack.currentWidget() == self.rp_done:
            self.save_rp_profile(self._rp_answers)
            self.rp_done_lbl.setText(
                "Saved to rp_profile.json ✅\n\n"
                f"Answers: {json.dumps(self._rp_answers, ensure_ascii=False)}"
            )

    def _rp_restart(self):
        self._rp_answers = {}
        if hasattr(self, "rp_stack"):
            self.rp_stack.setCurrentIndex(0)

    def _rp_load_into_ui(self):
        data = self.load_rp_profile()
        ans = data.get("answers", {})
        if ans:
            # show as info only (we won't auto-skip steps to keep demo clean)
            if hasattr(self, "rp_done_lbl"):
                self.rp_done_lbl.setText(
                    "Saved profile found ✅\n\n"
                    f"{json.dumps(ans, ensure_ascii=False)}"
                )

    # ---------- Medical History logic ----------

    def _add_injection_entry(self):
        hist = self.load_medical_history()

        date = self.inj_date.text().strip()
        med = self.inj_med.text().strip()
        notes = self.inj_notes.text().strip()

        eye = "Unspecified"
        if self.inj_eye_left.isChecked():
            eye = "Left"
        elif self.inj_eye_right.isChecked():
            eye = "Right"

        if not date or not med:
            QMessageBox.warning(self, "Missing", "Please enter date and injection/medicine name.")
            return

        hist["injections"].append({
            "date": date,
            "eye": eye,
            "medicine": med,
            "notes": notes
        })
        self.save_medical_history(hist)

        self.inj_med.setText("")
        self.inj_notes.setText("")
        self.inj_eye_left.setChecked(False)
        self.inj_eye_right.setChecked(False)

        self._refresh_medical_history_labels()

    def _add_medicine_entry(self):
        hist = self.load_medical_history()

        name = self.med_name.text().strip()
        dose = self.med_dose.text().strip()
        freq = self.med_freq.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing", "Please enter medicine name.")
            return

        hist["medicines"].append({
            "name": name,
            "dosage": dose,
            "frequency": freq
        })
        self.save_medical_history(hist)

        self.med_name.setText("")
        self.med_dose.setText("")
        self.med_freq.setText("")

        self._refresh_medical_history_labels()

    def _refresh_medical_history_labels(self):
        hist = self.load_medical_history()
        inj = hist.get("injections", [])
        meds = hist.get("medicines", [])

        if hasattr(self, "inj_list_lbl"):
            if not inj:
                self.inj_list_lbl.setText("No injection entries yet.")
            else:
                last = inj[-5:]
                lines = []
                for it in reversed(last):
                    extra = f" — {it.get('notes','')}" if it.get("notes") else ""
                    lines.append(f"• {it.get('date','')} | {it.get('eye','')} | {it.get('medicine','')}{extra}")
                self.inj_list_lbl.setText("\n".join(lines))

        if hasattr(self, "med_list_lbl"):
            if not meds:
                self.med_list_lbl.setText("No medicines yet.")
            else:
                lastm = meds[-6:]
                lines = []
                for it in reversed(lastm):
                    s = f"• {it.get('name','')}"
                    if it.get("dosage"):
                        s += f" — {it.get('dosage')}"
                    if it.get("frequency"):
                        s += f" ({it.get('frequency')})"
                    lines.append(s)
                self.med_list_lbl.setText("\n".join(lines))


    def build_sidebar(self):
        side = QGroupBox("Menu")
        side.setFixedWidth(290)
        v = QVBoxLayout(side)
        v.setSpacing(14)

        brand = QLabel("LightSensePro")
        brand.setStyleSheet("font-size: 22px; font-weight: 1000; color:#0b1220;")
        v.addWidget(brand)
        phone_hint = QLabel(
                "Connect your phone camera using\n"
                "IP Webcam or similar camera apps"
        )

        phone_hint.setWordWrap(True)

        phone_hint.setStyleSheet("""
            font-size: 20px;
            color: #475569;
            font-weight: 500;
        """)

        v.addWidget(phone_hint)
        v.addWidget(hline())

        def nav_btn(text, idx):
            b = QPushButton(text)
            b.clicked.connect(lambda: self.switch_page(idx))
            return b
        v.addWidget(nav_btn("Home", 0))

        v.addWidget(nav_btn("Reading Assist", 2))

        v.addWidget(nav_btn("Voice Assist", 8))
        comfort_btn = QPushButton("Comfort Profile")
        comfort_btn.clicked.connect(lambda: self.switch_page(6))
        v.addWidget(comfort_btn)

        health_btn = QPushButton("Health")
        health_btn.clicked.connect(
            lambda: self.center_stack.setCurrentWidget(self.page_health)
        )
        v.addWidget(health_btn)
        v.addStretch(1)

        back_btn = QPushButton("Back to Welcome")
        back_btn.clicked.connect(self.back_to_welcome)
        v.addWidget(back_btn)

        logout_btn = QPushButton("Logout")
        logout_btn.clicked.connect(self.logout_user)
        v.addWidget(logout_btn)

        return side
    
    # ---------------- PERIPHERAL---------------

    def build_page_peripheral_wide(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        card = QGroupBox("Peripheral")
        c = QVBoxLayout(card)
        c.setContentsMargins(14, 14, 14, 14)
        c.setSpacing(10)

        # --- Top bar ---
        top = QHBoxLayout()

        btn_back = QPushButton("Back")
        btn_back.clicked.connect(lambda: self.switch_page(0))

        btn_stop = QPushButton("Stop Camera")
        btn_stop.clicked.connect(self.toggle_camera)
        self._register_camera_button(btn_stop)

        top.addWidget(btn_back)
        top.addStretch(1)
        top.addWidget(btn_stop)
        c.addLayout(top)

        c.addWidget(big_title("Peripheral"))
        c.addWidget(small_desc(
            "Peripheral Alert detects motion entering from Left/Right and gives sound + visual alert.\n"
            "Wide Field Compression squeezes edges toward center so more scene stays in view."
        ))
        c.addWidget(hline())

        # --- Peripheral controls ---
        self.cb_peripheral = QCheckBox("Enable Peripheral Alert")
        self.cb_peripheral.setChecked(False)

        c.addWidget(self.cb_peripheral)

        # live status label inside panel
        self.periph_status = QLabel("Status: Ready")
        self.periph_status.setStyleSheet("color:#334155; font-size: 16px; font-weight: 900;")
        c.addWidget(self.periph_status)

        # Reset row
        reset_row = QHBoxLayout()
        reset_row.addStretch(1)

        btn_reset = QPushButton("Reset")
        btn_reset.setFixedWidth(140)
        btn_reset.clicked.connect(self.reset_peripheral_wide)
        reset_row.addWidget(btn_reset)
        c.addLayout(reset_row)

        v.addWidget(card)
        v.addStretch(1)
        return box

    def _update_peripheral_wide_flags(self):
        self.peripheral_on = bool(getattr(self, "cb_peripheral", None) and self.cb_peripheral.isChecked())
        # reset motion baseline when toggling
        self._prev_gray = None
        self._motion_text = ""
        self._motion_text_ts = 0

        if hasattr(self, "periph_status"):
            s = []
            if self.peripheral_on:
                s.append("Peripheral ON")
            self.periph_status.setText("Status: " + (" | ".join(s) if s else "Ready"))

        self.on_status("Peripheral updated.")

    def reset_peripheral_wide(self):
        if hasattr(self, "cb_peripheral"):
            self.cb_peripheral.setChecked(False)
        self._prev_gray = None
        self._motion_text = ""
        self._motion_text_ts = 0

        if hasattr(self, "periph_status"):
            self.periph_status.setText("Status: Ready")

        self.on_status("Peripheral reset.")


    def logout_user(self):
        log_event("User logged out")

        if self.worker_started:
            self.stop_camera_preview()

        self.current_user = None
        self.update_user_badge()

        self.root_stack.setCurrentWidget(self.login_page)

    def toggle_sidebar(self):
        if not hasattr(self, "sidebar"):
            return

        if self.sidebar.isVisible():
            self.sidebar.setVisible(False)
            self.sidebar.setFixedWidth(0)
        else:
            self.sidebar.setFixedWidth(250)
            self.sidebar.setVisible(True)

    def switch_page(self, idx: int):
        # Home full-screen
        if idx == 0:
            self.center_stack.setCurrentIndex(0)
            return

        if idx == 6:
            self.center_stack.setCurrentIndex(2)
            self.start_rp_intro()
            return
        if idx == 4:   # About page
            self.center_stack.setCurrentIndex(3)   # FULL PAGE (no camera)
            self.page_stack.setCurrentWidget(self.page_about)
            return

        if idx == 7:
            self.center_stack.setCurrentIndex(3)   # inj_full
            self.refresh_injections_history()
            return
        if idx == 8:
            self.center_stack.setCurrentIndex(3)
            return

        self.center_stack.setCurrentIndex(1)       # normal (camera)
        self.page_stack.setCurrentIndex(idx)
        self.animate_right_panel()


    def animate_right_panel(self):
        w = self.page_stack
        g = w.geometry()
        start = QRect(g.x() + 55, g.y(), g.width(), g.height())
        end = QRect(g.x(), g.y(), g.width(), g.height())

        anim = QPropertyAnimation(w, b"geometry", self)
        anim.setDuration(190)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._anim_refs.append(anim)

    # ---------------- HOME POPUP (PERIPHERAL/WIDE) ----------------

    def show_home_camera_popup(self, label_text="Camera Preview"):
        self.ensure_worker_started()

        if not hasattr(self, "_home_preview_wrap"):
            return

        self._home_preview_title.setText(label_text)
        if not self._home_preview_wrap.isVisible():
            self._home_preview_wrap.setVisible(True)
            QTimer.singleShot(0, lambda: self.animate_popup(self._home_preview_wrap, dy=10, dur=120))

    def hide_home_camera_popup(self):
        if hasattr(self, "_home_preview_wrap"):
            self._home_preview_wrap.hide()

    # ---------------- PAGES ----------------

    def build_page_home(self):
        box = QWidget()
        box.setStyleSheet("QWidget { background: #ffffff; }")
        root = QVBoxLayout(box)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        top_row = QHBoxLayout()
        menu_btn = QPushButton("☰")
        menu_btn.setFixedSize(44, 44)
        menu_btn.clicked.connect(self.toggle_sidebar)
        mic_btn = QPushButton("🎤")
        mic_btn.setFixedSize(66, 66)
        mic_btn.setStyleSheet("""
        QPushButton {
            font-size: 28px;
            font-weight: 900;

            background-color: #2563eb;
            color: white;

            border: none;
            border-radius: 33px;
        }

        QPushButton:hover {
            background-color: #1d4ed8;
        }

        QPushButton:pressed {
            background-color: #1e40af;
        }
    """)
        mic_btn.clicked.connect(self.start_listening)
        top_row.addWidget(menu_btn)     # left
        top_row.addStretch()            # pushes mic to right
        top_row.addWidget(mic_btn)      # right

        badge = self.build_user_badge()
        top_row.addWidget(badge, alignment=Qt.AlignLeft)

        top_row.addStretch(1)
        root.addLayout(top_row)

        title = QLabel("HOME")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 44px; font-weight: 1000; color: #0b1220;")
        root.addWidget(title)

        root.addWidget(hline())

        row = QHBoxLayout()
        row.setSpacing(14)

        cards_wrap = QFrame()
        cards_wrap.setStyleSheet("QFrame { background: transparent; }")
        cards_v = QVBoxLayout(cards_wrap)
        cards_v.setContentsMargins(0, 0, 0, 0)
        cards_v.setSpacing(12)

        def blue_card(title_text, on_click=None):
            f = QFrame()
            f.setStyleSheet("""
                QFrame {
                    background: #eaf2ff;
                    border-radius: 28px;
                }
            """)
            f.setMinimumSize(260, 220)
            f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

            lay = QVBoxLayout(f)
            lay.setContentsMargins(20, 20, 20, 20)
            lay.setSpacing(12)

            # Title
            tt = QLabel(title_text)
            tt.setAlignment(Qt.AlignCenter)
            tt.setStyleSheet("""
                font-size: 20px;
                font-weight: 1000;
                color:#0b1220;
            """)

            # Button
            btn = QPushButton("Open")
            btn.setMinimumHeight(45)
            btn.setStyleSheet("""
                QPushButton {
                    background: #2563eb;
                    color: white;
                    border-radius: 14px;
                    font-size: 16px;
                    font-weight: 900;
                }
                QPushButton:hover { background: #1d4ed8; }
            """)
            if on_click:
                btn.clicked.connect(on_click)

            lay.addStretch(1)
            lay.addWidget(tt)
            lay.addStretch(1)
            lay.addWidget(btn)

            return f

        def open_reading():
            self.ensure_worker_started() #reading pae open hoga
            self.switch_page(2)
            
        grid = QVBoxLayout()
        grid.setSpacing(14)

        row1 = QHBoxLayout()
        row2 = QHBoxLayout()

        # ---------- ACTIONS ----------
        def open_reading():
            self.ensure_worker_started()
            self.switch_page(2)

        def open_voice():
            self.switch_page(8)

        def open_peripheral():
            self.show_home_camera_popup("| Peripheral |")
            self.peripheral_on = True
        def open_health():
            self.center_stack.setCurrentWidget(self.page_health)

        def open_comfort():
            self.switch_page(6)

        # ---------- CARDS ----------
        card_reading = blue_card("Reading Assist", open_reading)
        card_voice = blue_card("Voice Assist", open_voice)
        card_peripheral = blue_card("Peripheral Mode", open_peripheral)
        card_health = blue_card("Health", open_health)
        card_comfort = blue_card("Comfort Profile", open_comfort)

        # ---------- GRID ----------
        row1.addWidget(card_reading)
        row1.addWidget(card_voice)

        row2.addWidget(card_peripheral)
        row2.addWidget(card_health)
        row2.addWidget(card_comfort)

        grid.addLayout(row1)
        grid.addLayout(row2)

        cards_v.addLayout(grid)

        cards_v.addStretch(1)

        preview_wrap = QFrame()
        preview_wrap.setVisible(False)
        preview_wrap.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border-radius: 18px;
            }
        """)
        preview_wrap.setMinimumWidth(420)
        preview_wrap.setMaximumWidth(500)
        preview_wrap.setMinimumHeight(520)
        preview_wrap.setMaximumHeight(800)
        preview_wrap.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        pv = QVBoxLayout(preview_wrap)
        pv.setContentsMargins(14, 14, 14, 14)
        pv.setSpacing(10)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        back_btn = QPushButton("Back")
        back_btn.setFixedHeight(42)
        back_btn.setMinimumWidth(90)
        back_btn.clicked.connect(self.hide_home_camera_popup)

        preview_title = QLabel("Camera Preview")
        preview_title.setAlignment(Qt.AlignCenter)
        preview_title.setStyleSheet("font-size: 18px; font-weight: 1000; color:#0b1220;")

        stop_btn = QPushButton("Stop Camera")
        stop_btn.setFixedHeight(42)
        stop_btn.setMinimumWidth(130)
        stop_btn.clicked.connect(self.toggle_camera)
        self._register_camera_button(stop_btn)

        header_layout.addWidget(back_btn)
        header_layout.addWidget(preview_title)
        header_layout.addWidget(stop_btn)

        pv.addWidget(header)

        mini = QLabel("Preview")
        mini.setAlignment(Qt.AlignCenter)
        mini.setMinimumSize(400, 320)
        mini.setMaximumSize(500, 400)
        # ---------------- Peripheral + Wide Field controls (inside popup) ----------------
        controls = QFrame()
        controls.setStyleSheet("""
            QFrame { background: #f8fafc; border-radius: 14px; }
        """)
        cl = QVBoxLayout(controls)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(8)

        self.cb_peripheral = QCheckBox("Peripheral Alert (Left/Right)")
        self.cb_peripheral.setChecked(True)
        self.cb_peripheral.stateChanged.connect(lambda: setattr(self, "peripheral_on", self.cb_peripheral.isChecked()))

        self.periph_status = QLabel("Status: Ready")
        self.periph_status.setStyleSheet("color:#334155; font-size: 20px; font-weight: 1000;")
        self.periph_status.setWordWrap(True)


        cl.addWidget(self.cb_peripheral)
        cl.addWidget(self.periph_status)

        pv.addWidget(controls)

        mini.setStyleSheet(
            "background:#0b1220; border:1px solid #e5e7eb; border-radius:14px; color:#e5e7eb; font-weight:900;"
        )
        pv.addWidget(mini, 1)

        self._home_cards_wrap = cards_wrap
        self._home_preview_wrap = preview_wrap
        self._home_mini_preview = mini
        self._home_preview_title = preview_title
        self._home_back_btn = back_btn
        self._home_stop_btn = stop_btn

        row.addWidget(cards_wrap, 1)
        row.addWidget(preview_wrap, 0)

        root.addLayout(row, 1)

        return box

    # ---------------- VISUAL MODES ----------------

    def build_page_visual_modes(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        card = QGroupBox("Visual Modes")
        c = QVBoxLayout(card)

        top = QHBoxLayout()
        btn_back = QPushButton("Back")
        btn_back.clicked.connect(self.back_from_reading)

        btn_cam = QPushButton("Stop Camera")
        btn_cam.clicked.connect(self.toggle_camera)
        self._register_camera_button(btn_cam)

        top.addWidget(btn_back)
        top.addStretch(1)
        top.addWidget(btn_cam)
        c.addLayout(top)

        c.addWidget(big_title("Visual Modes"))
        c.addWidget(small_desc(
            "Adaptive Brightness uses sliders. RP Visual Filter reduces glare by compressing harsh highlights "
            "and applying a mild comfort tone."
        ))
        c.addWidget(hline())

        self.cb_adaptive = QCheckBox("Adaptive Brightness (Slider Mode)")
        self.cb_filter = QCheckBox("RP Visual Filter (Glare Reduction)")
        self.cb_sharp = QCheckBox("Sharpness Enhancement")

        self.cb_adaptive.setChecked(self.settings.adaptive_on)
        self.cb_filter.setChecked(self.settings.filter_on)
        self.cb_sharp.setChecked(self.settings.sharpness_on)

        for cb in [self.cb_adaptive, self.cb_filter, self.cb_sharp]:
            cb.stateChanged.connect(self.update_from_ui)

        c.addWidget(self.cb_adaptive)
        c.addWidget(self.cb_filter)
        c.addWidget(self.cb_sharp)

        self.slider_brightness = self.make_slider_float("Brightness", 60, 220, self.settings.brightness, scale=100)
        self.slider_contrast = self.make_slider_float("Contrast (mild)", 100, 145, self.settings.contrast, scale=100)
        self.slider_sharpness = self.make_slider_float("Sharpness", 0, 120, self.settings.sharpness, scale=100)

        self.slider_brightness["slider"].valueChanged.connect(self.update_from_ui)
        self.slider_contrast["slider"].valueChanged.connect(self.update_from_ui)
        self.slider_sharpness["slider"].valueChanged.connect(self.update_from_ui)

        c.addWidget(self.slider_brightness["group"])
        c.addWidget(self.slider_contrast["group"])
        c.addWidget(self.slider_sharpness["group"])

        v.addWidget(card)
        v.addStretch(1)
        return box

    # ---------------- READING PAGE ----------------

    def build_page_reading(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(16)

        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border-radius: 20px;
            }
        """)

        c = QVBoxLayout(card)
        c.setContentsMargins(18, 18, 18, 18)
        c.setSpacing(16)

        # ---------------- TOP BAR ----------------
        top = QHBoxLayout()

        btn_back = QPushButton("Back")
        btn_back.setMinimumHeight(46)
        btn_back.clicked.connect(self.back_from_reading)

        btn_stop = QPushButton("Stop Camera")
        btn_stop.setMinimumHeight(50)
        btn_stop.clicked.connect(self.toggle_camera)
        self._register_camera_button(btn_stop)

        top.addWidget(btn_back)
        top.addStretch(1)
        top.addWidget(btn_stop)

        c.addLayout(top)

        # ---------------- IP CAMERA CARD ----------------
        ip_card = QFrame()
        ip_card.setStyleSheet("""
            QFrame {
                background: #f8fafc;
                border-radius: 16px;
            }
        """)

        ip_lay = QVBoxLayout(ip_card)
        ip_lay.setContentsMargins(14, 14, 14, 14)
        ip_lay.setSpacing(10)

        ip_label = QLabel("Phone Camera")
        ip_label.setStyleSheet("font-weight:900;")

        self.reading_ip_input = QLineEdit()
        self.reading_ip_input.setPlaceholderText("192.168.1.2:8080")
        self.reading_ip_input.setMinimumHeight(40)

        btn_row = QHBoxLayout()

        btn_connect = QPushButton("Connect")
        btn_connect.setMinimumHeight(46)
        btn_connect.setStyleSheet("background:#22c55e; color:white; border-radius:10px;")
        btn_connect.clicked.connect(self.connect_reading_ip_camera)

        btn_disconnect = QPushButton("Disconnect")
        btn_disconnect.setMinimumHeight(46)
        btn_disconnect.setStyleSheet("background:#ef4444; color:white; border-radius:10px;")
        btn_disconnect.clicked.connect(self.disconnect_reading_ip_camera)

        btn_row.addWidget(btn_connect)
        btn_row.addWidget(btn_disconnect)

        ip_lay.addWidget(ip_label)
        ip_lay.addWidget(self.reading_ip_input)
        ip_lay.addLayout(btn_row)

        c.addWidget(ip_card)

        # ---------------- ENABLE ----------------
        self.cb_reading = QCheckBox("Enable Reading Mode")
        self.cb_reading.setChecked(self.settings.reading_on)
        self.cb_reading.stateChanged.connect(self.update_from_ui)

        c.addWidget(self.cb_reading)

        # ---------------- SLIDER CARD ----------------
        slider_card = QFrame()
        slider_card.setStyleSheet("""
            QFrame {
                background: #f8fafc;
                border-radius: 16px;
                
            }
        """)

        s = QVBoxLayout(slider_card)
        s.setContentsMargins(14, 14, 14, 14)
        s.setSpacing(12)

        slider_title = QLabel("Reading Controls")
        slider_title.setStyleSheet("font-weight:1000; font-size:20px; color:#0b1220;")

        self.slider_zoom = self.make_slider_float("Zoom Level", 10, 40, self.settings.zoom, scale=10)
        self.slider_read_sharp = self.make_slider_float("Reading Sharpness", 80, 180, self.settings.reading_sharpness, scale=100)
        self.slider_read_edges = self.make_slider_float("Text Edge Highlight", 0, 100, self.settings.reading_edges, scale=100)
        self.slider_edge_thick = self.make_slider_int("Edge Thickness", 1, 5, int(self.settings.reading_edge_thickness))

        for sld in [self.slider_zoom, self.slider_read_sharp, self.slider_read_edges]:
            sld["slider"].valueChanged.connect(self.update_from_ui)

        self.slider_edge_thick["slider"].valueChanged.connect(self.update_from_ui)

        s.addWidget(slider_title)
        s.addWidget(self.slider_zoom["group"])
        s.addWidget(self.slider_read_sharp["group"])
        s.addWidget(self.slider_read_edges["group"])
        s.addWidget(self.slider_edge_thick["group"])

        c.addWidget(slider_card)

        # ---------------- RESET ----------------
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)  # push buttons to right
        # Reset Button
        btn_reset = QPushButton("Reset")
        btn_reset.setMinimumHeight(45)
        btn_reset.setStyleSheet("""
            QPushButton {
                background:#e5e7eb;
                border-radius:12px;
                font-weight:900;
            }
        """)
        btn_reset.clicked.connect(self.reset_reading_settings)

        btn_row.addWidget(btn_stop)
        btn_row.addWidget(btn_reset)

        # add row to layout
        c.addLayout(btn_row)
        v.addWidget(card)
        v.addStretch(1)

        return box

    
    def use_laptop_camera(self):
        self.restart_camera(0)
        self.on_status("Switched to laptop camera.")
    
    def back_from_reading(self):
        # go back to Home
        self.switch_page(0)

        # ensure webcam for all other modes
        self.restart_camera(0)

    def connect_reading_ip_camera(self):
        url = self.reading_ip_input.text().strip()

        if not url:
            self.on_status("Enter IP (e.g. 192.168.1.2:8080) first.")
            return

        # allow user to paste just 192.168.1.2:8080
        if not url.startswith("http"):
            url = "http://" + url

        # if user entered base only, append /video
        if url.endswith(":8080") or url.endswith(":8080/"):
            url = url.rstrip("/") + "/video"

        # restart camera to phone stream (Reading only)
        self.restart_camera(url)
        self.on_status("Phone camera connected for Reading Mode.")

    def disconnect_reading_ip_camera(self):
        self.restart_camera(0)
        self.on_status("Switched back to laptop webcam")

    def reset_reading_settings(self):
        defaults = AppSettings()

        # checkbox
        if hasattr(self, "cb_reading"):
            self.cb_reading.setChecked(defaults.reading_on)

        # sliders (match your scales)
        if hasattr(self, "slider_zoom"):
            self.slider_zoom["slider"].setValue(int(defaults.zoom * self.slider_zoom["scale"]))
        if hasattr(self, "slider_read_sharp"):
            self.slider_read_sharp["slider"].setValue(int(defaults.reading_sharpness * self.slider_read_sharp["scale"]))
        if hasattr(self, "slider_read_edges"):
            self.slider_read_edges["slider"].setValue(int(defaults.reading_edges * self.slider_read_edges["scale"]))
        if hasattr(self, "slider_edge_thick"):
            self.slider_edge_thick["slider"].setValue(int(defaults.reading_edge_thickness))

        # apply + update labels + push to worker
        self.update_from_ui()
        self.on_status("Reading settings reset.")
  # ---------------- PROFILES / ABOUT ----------------

    def build_page_profiles(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        card = QGroupBox("Profiles")
        c = QVBoxLayout(card)
        c.addWidget(big_title("Comfort Profiles"))
        c.addWidget(small_desc("Save and load your preferred settings."))
        c.addWidget(hline())

        btn_save = QPushButton("Save Comfort Profile")
        btn_save.setObjectName("primary")
        btn_load = QPushButton("Load Comfort Profile")

        btn_save.clicked.connect(self.save_profile_ui)
        btn_load.clicked.connect(self.load_profile_ui)

        c.addWidget(btn_save)
        c.addWidget(btn_load)

        v.addWidget(card)
        v.addStretch(1)
        return box

    def build_page_about(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        card = QGroupBox("About")
        c = QVBoxLayout(card)
        c.addWidget(big_title("About LightSensePro"))
        c.addWidget(small_desc(
            "LightSensePro is a real-time assistive vision prototype for RP.\n"
            "It demonstrates controlled brightness enhancement, glare reduction, reading assist, and emergency visibility."
        ))
        c.addWidget(hline())
        c.addWidget(small_desc("Tech: Python + OpenCV + PyQt5 | UI → Video Thread → Processing Pipeline"))

        v.addWidget(card)
        v.addStretch(1)
        return box

    # ---------------- SLIDERS ----------------

    def make_slider_float(self, title, min_v, max_v, init_float, scale=100):
        group = QFrame()
        v = QVBoxLayout(group)
        #title
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("""
            font-weight: 2000;
            font-size: 20px;
            color: #0b1220;
        """)

        label = QLabel(f"{init_float:.2f}")
        label.setStyleSheet("""
            QLabel {
                border: none;
                background: transparent;
                color:#334155;
                font-size: 18px;
                font-weight: 1000;
                color: #1e293b;
            }
        """)
        label.setAlignment(Qt.AlignCenter)

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(min_v)
        slider.setMaximum(max_v)
        slider.setValue(int(init_float * scale))
        slider.setFixedHeight(50)

        v.addWidget(title_lbl)
        v.addWidget(slider)
        v.addWidget(label)
        return {"group": group, "slider": slider, "label": label, "scale": scale}

    def make_slider_int(self, title, min_v, max_v, init_int):
        group = QFrame()
        group.setStyleSheet("QFrame { border: none; background: transparent; }")
        v = QVBoxLayout(group)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("""
            font-weight: 2000;
            font-size: 20px;
            color: #0b1220;
        """)
        

        label = QLabel(str(init_int))
        label.setStyleSheet("""
            QLabel {
                border: none;
                background: transparent;
                color:#334155;
                font-size: 18px;
                font-weight: 1000;
                color: #1e293b;
            }
        """)
        label.setAlignment(Qt.AlignCenter)

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(min_v)
        slider.setMaximum(max_v)
        slider.setValue(int(init_int))
        slider.setFixedHeight(50)

        v.addWidget(title_lbl)        
        v.addWidget(slider)
        v.addWidget(label)
        return {"group": group, "slider": slider, "label": label}

    # ---------------- USER BADGE ----------------

    def build_user_badge(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        circle = QLabel("●")
        circle.setStyleSheet("font-size: 24px; color:#22c55e; font-weight:1000;")

        name = QLabel("Guest")
        name.setStyleSheet("font-size: 20px; font-weight: 950; color:#0b1220;")

        lay.addWidget(circle)
        lay.addWidget(name)

        self._user_badges.append((name, circle))
        self.update_user_badge()

        return w

    def update_user_badge(self):
        username = "Guest"
        if self.current_user and "username" in self.current_user:
            username = self.current_user["username"]

        for name_lbl, circle_lbl in self._user_badges:
            name_lbl.setText(username)

    # ---------------- ACTIONS ----------------

    def enter_app(self):
        # Keep current_user if logged in (don't overwrite)
        if self.current_user and "username" in self.current_user:
            log_event(f"Entered app as {self.current_user['username']}")
        else:
            log_event("Entered app as Guest")

        self.update_user_badge()

        self.root_stack.setCurrentWidget(self.splash_page)
        self.start_splash_animation()

        def go_welcome():
            if self._dots_timer:
                self._dots_timer.stop()

            self.root_stack.setCurrentWidget(self.post_welcome_page)
            QTimer.singleShot(0, self.animate_post_welcome_card)

            def go_home():
                self.root_stack.setCurrentWidget(self.app_page)
                self.switch_page(0)

            QTimer.singleShot(3000, go_home)

        QTimer.singleShot(2000, go_welcome)


    def back_to_welcome(self):
        log_event("Back to Welcome")
        self.root_stack.setCurrentWidget(self.welcome_page)
        QTimer.singleShot(0, self.animate_welcome_card)

    def update_from_ui(self):
        if hasattr(self, "cb_adaptive"):
            self.settings.adaptive_on = self.cb_adaptive.isChecked()
        if hasattr(self, "cb_filter"):
            self.settings.filter_on = self.cb_filter.isChecked()
        if hasattr(self, "cb_sharp"):
            self.settings.sharpness_on = self.cb_sharp.isChecked()
        if hasattr(self, "cb_reading"):
            self.settings.reading_on = self.cb_reading.isChecked()

        if hasattr(self, "slider_brightness"):
            sc = self.slider_brightness["scale"]
            self.settings.brightness = self.slider_brightness["slider"].value() / sc
            self.slider_brightness["label"].setText(f"{self.settings.brightness:.2f}")

        if hasattr(self, "slider_contrast"):
            sc = self.slider_contrast["scale"]
            self.settings.contrast = self.slider_contrast["slider"].value() / sc
            self.slider_contrast["label"].setText(f"{self.settings.contrast:.2f}")

        if hasattr(self, "slider_sharpness"):
            sc = self.slider_sharpness["scale"]
            self.settings.sharpness = self.slider_sharpness["slider"].value() / sc
            self.slider_sharpness["label"].setText(f"{self.settings.sharpness:.2f}")

        if hasattr(self, "slider_zoom"):
            sc = self.slider_zoom["scale"]
            self.settings.zoom = self.slider_zoom["slider"].value() / sc
            self.slider_zoom["label"].setText(f"{self.settings.zoom:.1f}")

        if hasattr(self, "slider_read_sharp"):
            sc = self.slider_read_sharp["scale"]
            self.settings.reading_sharpness = self.slider_read_sharp["slider"].value() / sc
            self.slider_read_sharp["label"].setText(f"{self.settings.reading_sharpness:.2f}")

        if hasattr(self, "slider_read_edges"):
            sc = self.slider_read_edges["scale"]
            self.settings.reading_edges = self.slider_read_edges["slider"].value() / sc
            self.slider_read_edges["label"].setText(f"{self.settings.reading_edges:.2f}")

        if hasattr(self, "slider_edge_thick"):
            self.settings.reading_edge_thickness = int(self.slider_edge_thick["slider"].value())
            self.slider_edge_thick["label"].setText(str(self.settings.reading_edge_thickness))

        emergency_on = self.settings.emergency_on
        for sl in ["slider_brightness", "slider_contrast", "slider_sharpness"]:
            if hasattr(self, sl):
                self.__dict__[sl]["slider"].setEnabled(not emergency_on)

        try:
            if hasattr(self, "worker") and self.worker:
                self.worker.update_settings(self.settings)
        except Exception:
            pass

    def get_active_profile_dir(self) -> str:
        if hasattr(self, "current_user") and self.current_user and "username" in self.current_user:
            return user_profile_dir(self.current_user["username"])
        return PROFILE_DIR
    
    def get_med_data_path(self) -> str:
        base_dir = self.get_active_profile_dir()
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, "medical_history.json")

    def load_med_data(self) -> dict:
        path = self.get_med_data_path()
        if not os.path.exists(path):
            return {"injections": [], "medicines": []}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"injections": [], "medicines": []}

    def save_med_data(self, data: dict):
        path = self.get_med_data_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def build_page_injections_history(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        card = QGroupBox("Medical History")
        c = QVBoxLayout(card)
        c.setContentsMargins(14, 14, 14, 14)
        c.setSpacing(10)

        # Top row
        top = QHBoxLayout()
        btn_back = QPushButton("Back")
        btn_back.clicked.connect(lambda: self.switch_page(0))
        top.addWidget(btn_back)
        top.addStretch(1)
        c.addLayout(top)

        # Title + green bar
        title = QLabel("Injections History")
        title.setAlignment(Qt.AlignLeft)
        title.setStyleSheet("font-size: 32px; font-weight: 1000; color:#0b1220;")
        c.addWidget(title)

        accent = QFrame()
        accent.setFixedHeight(6)
        accent.setMaximumWidth(180)
        accent.setStyleSheet("background:#22c55e; border-radius:3px;")
        c.addWidget(accent)

        sub = QLabel("Saved entries (date • eye • medicine • notes). Demo-ready tracking for RP/CME.")
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size: 15px; color:#334155; font-weight: 800;")
        c.addWidget(sub)

        c.addWidget(hline())

        # Scroll list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:0px; }")

        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)

        self.inj_list_label = QLabel("Loading...")
        self.inj_list_label.setWordWrap(True)
        self.inj_list_label.setStyleSheet("font-size: 16px; color:#0b1220; font-weight: 800;")
        cv.addWidget(self.inj_list_label)
        cv.addStretch(1)

        scroll.setWidget(content)
        c.addWidget(scroll, 1)

        v.addWidget(card, 1)

        # Fill list once page is built
        self.refresh_injections_history()

        return box

    def refresh_injections_history(self):
        data = self.load_medical_history()   # ✅ SAME SOURCE as add entry
        items = data.get("injections", [])

        if not hasattr(self, "inj_list_label"):
            return

        if not items:
            self.inj_list_label.setText("No injection entries yet.")
            return

        # latest first (safe sorting)
        items_sorted = sorted(items, key=lambda x: x.get("date", ""), reverse=True)

        lines = []
        for it in items_sorted:
            d = it.get("date", "—")
            eye = it.get("eye", "—")
            med = it.get("medicine", "—")
            notes = (it.get("notes", "") or "").strip()

            if notes:
                lines.append(f"• {d}  |  {eye}  |  {med}\n   Notes: {notes}")
            else:
                lines.append(f"• {d}  |  {eye}  |  {med}")

        self.inj_list_label.setText("\n\n".join(lines))



        # ---------------- RP PROFILE STORAGE ----------------

    def get_user_data_dir(self) -> str:
        if self.current_user and "username" in self.current_user:
            return user_profile_dir(self.current_user["username"])
        return guest_profile_dir()

    def rp_profile_path(self) -> str:
        return os.path.join(self.get_user_data_dir(), "rp_profile.json")

    def medical_history_path(self) -> str:
        return os.path.join(self.get_user_data_dir(), "medical_history.json")

    def load_rp_profile(self) -> dict:
        return _load_json(self.rp_profile_path(), {"answers": {}, "updated_at": ""})

    def save_rp_profile(self, answers: dict):
        data = {
            "answers": answers,
            "updated_at": datetime.now().isoformat(timespec="seconds")
        }
        _save_json(self.rp_profile_path(), data)

    def load_medical_history(self) -> dict:
        return _load_json(self.medical_history_path(), {"injections": [], "medicines": [], "updated_at": ""})

    def save_medical_history(self, hist: dict):
        hist["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_json(self.medical_history_path(), hist)


    def save_profile_ui(self):
        base_dir = self.get_active_profile_dir()
        os.makedirs(base_dir, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save Profile", base_dir, "JSON (*.json)")
        if not path:
            return
        save_profile(self.settings, path)
        log_event(f"Profile saved: {path}")
        QMessageBox.information(self, "Saved", "Profile saved successfully.")

    def load_profile_ui(self):
        base_dir = self.get_active_profile_dir()
        os.makedirs(base_dir, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Load Profile", base_dir, "JSON (*.json)")
        if not path:
            return
        self.settings = load_profile(path)

        if hasattr(self, "cb_adaptive"):
            self.cb_adaptive.setChecked(self.settings.adaptive_on)
        if hasattr(self, "cb_filter"):
            self.cb_filter.setChecked(self.settings.filter_on)
        if hasattr(self, "cb_sharp"):
            self.cb_sharp.setChecked(self.settings.sharpness_on)
        if hasattr(self, "cb_reading"):
            self.cb_reading.setChecked(self.settings.reading_on)

        if hasattr(self, "btn_emergency"):
            self.btn_emergency.setChecked(self.settings.emergency_on)

        if hasattr(self, "slider_brightness"):
            self.slider_brightness["slider"].setValue(int(self.settings.brightness * self.slider_brightness["scale"]))
        if hasattr(self, "slider_contrast"):
            self.slider_contrast["slider"].setValue(int(self.settings.contrast * self.slider_contrast["scale"]))
        if hasattr(self, "slider_sharpness"):
            self.slider_sharpness["slider"].setValue(int(self.settings.sharpness * self.slider_sharpness["scale"]))

        if hasattr(self, "slider_zoom"):
            self.slider_zoom["slider"].setValue(int(self.settings.zoom * self.slider_zoom["scale"]))
        if hasattr(self, "slider_read_sharp"):
            self.slider_read_sharp["slider"].setValue(int(self.settings.reading_sharpness * self.slider_read_sharp["scale"]))
        if hasattr(self, "slider_read_edges"):
            self.slider_read_edges["slider"].setValue(int(self.settings.reading_edges * self.slider_read_edges["scale"]))
        if hasattr(self, "slider_edge_thick"):
            self.slider_edge_thick["slider"].setValue(int(self.settings.reading_edge_thickness))

        self.update_from_ui()
        log_event(f"Profile loaded: {path}")
        QMessageBox.information(self, "Loaded", "Profile loaded successfully.")

    # ---------------- VIDEO CALLBACKS ----------------

    def on_status(self, msg: str):
        if hasattr(self, "status_label"):
            self.status_label.setText(f"Status: {msg}")

    def on_frame(self, frame_bgr):
            # ✅ Flip ONLY when NOT in reading mode
        if not getattr(self.settings, "reading_on", False):
            frame_bgr = cv2.flip(frame_bgr, 1)

            # processing
        frame_bgr = self._apply_peripheral_and_widefield(frame_bgr)

        #isse camera mirror ni hoga
        if hasattr(self, "video_label") and self.video_label is not None:
                # 🔥 force frame to match label size
            label_w = self.video_label.width()
            label_h = self.video_label.height()

            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qimg)
        # ✅ Reading Mode → FULL SIZE
        if getattr(self.settings, "reading_on", False):
            self.video_label.setPixmap(
                pix.scaled(
                    self.video_label.size(),
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation
                )
            )

        # ✅ Peripheral / other modes → controlled size
        else:
            self.video_label.setPixmap(
                pix.scaled(
                    900, 600,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            )
        if hasattr(self, "_home_mini_preview") and self._home_mini_preview.isVisible():
            self._home_mini_preview.setPixmap(
                pix.scaled(
                    self._home_mini_preview.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            )
        

    def closeEvent(self, event):
        try:
            if self.worker_started:
                self.worker.stop()
                try:
                    self.worker.wait(1000)
                except Exception:
                    pass
        except Exception:
            pass
        event.accept()

    def _apply_peripheral_and_widefield(self, frame_bgr):
        now = int(datetime.now().timestamp() * 1000)

        # ---------- Wide Field Compression ----------
        if getattr(self, "widefield_on", False):
            h, w = frame_bgr.shape[:2]
            strength = 0.38  # strong but still usable

            # build mapping
            xs = (cv2.getGaussianKernel(w, w/6).reshape(-1) * 0)  # dummy to keep cv2 loaded
            x = (np.arange(w, dtype=np.float32) / (w - 1))
            x = x.reshape(1, -1).repeat(h, axis=0)

            # nonlinear squeeze toward center
            # map x from edges toward 0.5
            dx = x - 0.5
            x_map = 0.5 + dx * (1.0 - strength) + (dx**3) * strength

            if getattr(self, "widefield_mode", 1) == 2:
                y = (np.arange(h, dtype=np.float32) / (h - 1)).reshape(-1, 1).repeat(w, axis=1)
                dy = y - 0.5
                y_map = 0.5 + dy * (1.0 - strength) + (dy**3) * strength
            else:
                y_map = (np.arange(h, dtype=np.float32) / (h - 1)).reshape(-1, 1).repeat(w, axis=1)

            map_x = (x_map * (w - 1)).astype("float32")
            map_y = (y_map * (h - 1)).astype("float32")

            frame_bgr = cv2.remap(frame_bgr, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

        # ---------- Peripheral Motion Detection ----------
        if getattr(self, "peripheral_on", False):
            # downscale for speed
            small = cv2.resize(frame_bgr, (0, 0), fx=0.5, fy=0.5)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (9, 9), 0)

            if self._prev_gray is None:
                self._prev_gray = gray
                return frame_bgr

            diff = cv2.absdiff(self._prev_gray, gray)
            self._prev_gray = gray

            _, th = cv2.threshold(diff, 18, 255, cv2.THRESH_BINARY)
            th = cv2.dilate(th, None, iterations=2)

            hh, ww = th.shape[:2]
            band = max(18, int(ww * 0.04))  # left/right band width

            left_roi = th[:, :band]
            right_roi = th[:, ww - band:ww]

            left_score = int(cv2.countNonZero(left_roi))
            right_score = int(cv2.countNonZero(right_roi))

            # tune threshold
            trigger = 1400

            fired = None
            if now - self._last_motion_ts > self._motion_cooldown_ms:
                if left_score > trigger and left_score > right_score * 1.15:
                    fired = "Motion entering from LEFT"
                elif right_score > trigger and right_score > left_score * 1.15:
                    fired = "Motion entering from RIGHT"

            
            if fired:
                self._last_motion_ts = now
                self._motion_text = fired
                self._motion_text_ts = now

                # 🔊 VOICE ALERT
                try:
                    self.voice_engine.say(fired)
                    self.voice_engine.runAndWait()
                except Exception:
                    pass

                if hasattr(self, "periph_status"):
                    self.periph_status.setText("Status: " + fired)

            # draw overlay text for ~1.2s
            if self._motion_text and (now - self._motion_text_ts) < 1200:
                cv2.putText(
                    frame_bgr,
                    self._motion_text,
                    (30, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (0, 0, 255),
                    3,
                    cv2.LINE_AA
                )

        return frame_bgr


    # ---------------- ENTER SHORTCUT ----------------

    def keyPressEvent(self, event):
        try:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):

                # ✅ Login page enter should login
                if self.root_stack.currentWidget() == self.login_page:
                    self.handle_login()
                    return

                # Welcome page existing behaviour
                if self.root_stack.currentWidget() == self.welcome_page:
                    if hasattr(self, "_welcome_intro_stack") and self._welcome_intro_stack.currentIndex() == 1:
                        if hasattr(self, "_welcome_start_btn"):
                            self._welcome_start_btn.click()
                            return
        except Exception:
            pass

        super().keyPressEvent(event)
