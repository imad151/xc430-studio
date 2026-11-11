from pathlib import Path

_ICONS = (Path(__file__).parent / "icons").as_posix()   # forward slashes work in QSS on Windows too

ACCENT = "#4f9dff"
OK = "#3ecf8e"
WARN = "#f5b041"
BAD = "#ff5c5c"
MUTED = "#8a93a6"
BG = "#14171c"
PANEL = "#1c2027"
PANEL2 = "#242a33"
BORDER = "#2f3642"
TEXT = "#e6e9ef"

QSS = f"""
* {{ font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, QMessageBox, QInputDialog, QFileDialog {{ background: {BG}; }}
QMenuBar {{ background: {PANEL}; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {PANEL2}; }}
QMenu {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QTabWidget > QStackedWidget > QWidget {{ background: transparent; }}
QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; spacing: 6px; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {PANEL}; padding: 6px 10px; color: {MUTED}; font-weight: 600; }}
QFrame#card, QGroupBox, QFrame#panel {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
}}
QFrame#card[selected="true"] {{ border: 1px solid {ACCENT}; }}
QGroupBox {{ margin-top: 14px; padding: 12px 10px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
QPushButton {{
    background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton:disabled {{ color: #5a6170; }}
QPushButton#estop {{
    background: #c0392b; border: 2px solid #ff6b5b; color: white; font-weight: 800;
    padding: 8px 18px; border-radius: 8px; letter-spacing: 1px;
}}
QPushButton#estop:hover {{ background: #e74c3c; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#danger {{ border-color: {BAD}; color: {BAD}; }}
QPushButton#torque {{ min-width: 80px; font-weight: 600; }}
QPushButton#torque:checked {{ background: {OK}; border-color: {OK}; color: #0b1a12; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {ACCENT};
}}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}
QListWidget, QTableWidget, QPlainTextEdit {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: {BORDER};
    alternate-background-color: {PANEL2};
}}
QListWidget::item {{ padding: 8px; border-radius: 6px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: #2b4a75; }}
QHeaderView::section {{ background: {PANEL2}; border: none; border-right: 1px solid {BORDER}; padding: 5px; color: {MUTED}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; color: {MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL2}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; width: 16px; margin: -6px 0; border-radius: 8px; }}
QProgressBar {{ background: {PANEL2}; border: none; border-radius: 4px; height: 8px; text-align: center; font-size: 10px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QScrollArea {{ border: none; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid #56607a; border-radius: 4px; background: {PANEL2};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({_ICONS}/check.svg); }}
QCheckBox::indicator:disabled {{ border-color: {BORDER}; background: {PANEL}; }}
QCheckBox:disabled {{ color: #5a6170; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 18px; border: none; background: transparent;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {BORDER}; border-radius: 3px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({_ICONS}/up.svg); width: 10px; height: 6px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({_ICONS}/down.svg); width: 10px; height: 6px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: none; }}
QLabel#h1 {{ font-size: 16px; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#big {{ font-size: 22px; font-weight: 700; font-family: monospace; }}
QLabel#value {{ font-family: monospace; font-size: 13px; }}
QLabel#badge {{ border-radius: 9px; padding: 2px 8px; font-size: 11px; font-weight: 600; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
"""


def apply_theme(app):
    """Dark Fusion palette + QSS. The palette covers anything the stylesheet doesn't
    (window backgrounds, popups, disabled text), so nothing is left see-through."""
    from PySide6.QtGui import QColor, QPalette
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: PANEL, QPalette.AlternateBase: PANEL2,
        QPalette.Text: TEXT, QPalette.Button: PANEL2, QPalette.ButtonText: TEXT, QPalette.ToolTipBase: PANEL2,
        QPalette.ToolTipText: TEXT, QPalette.Highlight: ACCENT, QPalette.HighlightedText: "#ffffff",
        QPalette.PlaceholderText: MUTED, QPalette.Link: ACCENT, QPalette.BrightText: BAD,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#5a6170"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
