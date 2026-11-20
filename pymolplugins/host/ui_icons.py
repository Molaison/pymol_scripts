"""Small plugin control icons shared by the native Windows and macOS panels."""

from functools import lru_cache

from pymol.Qt import QtCore, QtGui


_SIZE = 16
# Each stroke is a polyline in the same 16-pixel grid.
_STROKES = {
    "left": (((10, 3.5), (5.5, 8), (10, 12.5)),),
    "right": (((6, 3.5), (10.5, 8), (6, 12.5)),),
    "down": (((3.5, 6), (8, 10.5), (12.5, 6)),),
    "window": (
        ((2.5, 12.5), (2.5, 3), (13.5, 3), (13.5, 12.5), (2.5, 12.5)),
        ((2.5, 6), (13.5, 6)),
        ((9, 6), (9, 12.5)),
    ),
    "folder": (((2, 12.5), (2, 4), (6, 4), (7.5, 6), (14, 6), (14, 12.5), (2, 12.5)),),
    "reload": (
        ((12.5, 5.5), (10.5, 3.5), (7, 3), (4, 4.5), (3, 7.5),
         (4, 11), (7, 13), (10.5, 12.5), (12.5, 10.5)),
        ((13, 2.5), (13, 6), (9.5, 6)),
    ),
    "info": (
        ((8, 2), (12, 3.5), (14, 8), (12, 12.5), (8, 14),
         (4, 12.5), (2, 8), (4, 3.5), (8, 2)),
        ((8, 5.3), (8, 5.5)),
        ((8, 7.5), (8, 10.8)),
    ),
}


@lru_cache(maxsize=None)
def _icon(name):
    icon = QtGui.QIcon()
    for modes, color in (
        ((QtGui.QIcon.Normal, QtGui.QIcon.Active, QtGui.QIcon.Selected), "#567da7"),
        ((QtGui.QIcon.Disabled,), "#8894a0"),
    ):
        for scale in (1, 2):
            pixmap = QtGui.QPixmap(_SIZE * scale, _SIZE * scale)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.scale(scale, scale)
            painter.setPen(QtGui.QPen(QtGui.QColor(color), 1.55, QtCore.Qt.SolidLine,
                                      QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
            for stroke in _STROKES[name]:
                painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(*point) for point in stroke]))
            painter.end()
            pixmap.setDevicePixelRatio(scale)
            for mode in modes:
                icon.addPixmap(pixmap, mode)
    return icon


def set_tool_icon(button, name):
    """Use the same shape and logical size regardless of the host Qt style."""
    button.setIconSize(QtCore.QSize(_SIZE, _SIZE))
    button.setIcon(_icon(name))
