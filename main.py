import sys
from PyQt5.QtWidgets import QApplication
from src.ui_main import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet("""
    QWidget {
        background: #ffffff;
        color: #111827;
        font-size: 14px;
    }

    QGroupBox {
        border: 1px solid #e5e7eb;
        border-radius: 16px;
        margin-top: 10px;
        padding: 12px;
        background: #ffffff;
    }

    QGroupBox::title {
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 6px;
        color: #111827;
        font-weight: 700;
    }

    QPushButton {
        background: #ffffff;
        border: 1px solid #d1d5db;
        padding: 10px 14px;
        border-radius: 12px;
        font-weight: 700;
    }

    QPushButton:hover {
        background: #f3f4f6;
    }

    QSlider::groove:horizontal {
        height: 6px;
        background: #e5e7eb;
        border-radius: 3px;
    }

    QSlider::handle:horizontal {
        width: 16px;
        margin: -6px 0;
        border-radius: 8px;
        background: #2563eb;
    }

    QLabel {
        background: transparent;
    }
    """)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
 