from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QPen, QIcon, QFont
from PyQt6.QtCore import Qt

ICON_SIZE = 64

def _make_icon(color, border_color):
    pix = QPixmap(ICON_SIZE, ICON_SIZE)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = 4
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(*border_color)))
    p.drawEllipse(margin, margin, ICON_SIZE - margin * 2, ICON_SIZE - margin * 2)

    inner = margin + 3
    p.setBrush(QBrush(QColor(*color)))
    p.drawEllipse(inner, inner, ICON_SIZE - inner * 2, ICON_SIZE - inner * 2)

    p.setPen(QPen(QColor(255, 255, 255)))
    p.setFont(QFont("Arial", 20, QFont.Weight.Bold))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "K")
    p.end()

    return QIcon(pix)

def icon_ok():
    return _make_icon((76, 175, 80), (56, 142, 60))

def icon_syncing():
    return _make_icon((255, 167, 38), (245, 124, 0))

def icon_error():
    return _make_icon((229, 57, 53), (198, 40, 40))
