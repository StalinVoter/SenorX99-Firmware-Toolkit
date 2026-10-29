"""Paint language-button icons without emoji fonts or SVG runtime plugins."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF, QRegion


def language_flag(code):
    image = QPixmap(120, 80)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    if code == 'en':
        p.fillRect(0, 10, 120, 60, QColor('#012169'))
        p.setClipRect(0, 10, 120, 60)
        p.setPen(QPen(QColor('white'), 12))
        p.drawLine(0, 10, 120, 70); p.drawLine(0, 70, 120, 10)
        p.setPen(Qt.NoPen); p.setBrush(QColor('#c8102e'))
        for points in (((0,10),(60,40),(48,40),(0,16)),
                       ((60,40),(120,70),(120,64),(72,40)),
                       ((0,70),(60,40),(60,46),(12,70)),
                       ((60,40),(120,10),(108,10),(60,34))):
            p.drawPolygon(QPolygonF([QPointF(*v) for v in points]))
        p.fillRect(50, 10, 20, 60, Qt.white); p.fillRect(0, 30, 120, 20, Qt.white)
        p.fillRect(54, 10, 12, 60, QColor('#c8102e')); p.fillRect(0, 34, 120, 12, QColor('#c8102e'))
    else:
        p.fillRect(3, 0, 114, 80, QColor('#009739'))
        p.setBrush(QColor('#fedf00'))
        p.drawPolygon(QPolygonF([QPointF(60,10),QPointF(107,40),QPointF(60,70),QPointF(13,40)]))
        p.setBrush(QColor('#002776')); p.drawEllipse(QPointF(60,40), 21, 21)
        p.save(); p.setClipRegion(QRegion(39,19,42,42,QRegion.Ellipse))
        p.setPen(QPen(Qt.white, 4)); p.drawArc(20,32,90,50,30*16,120*16); p.restore()
        p.setPen(QPen(Qt.white, 2))
        for x,y in ((49,45),(56,50),(63,47),(68,53),(60,57),(71,43),(46,39)):
            p.drawPoint(x,y)
    p.end()
    return QIcon(image)
