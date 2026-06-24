
"""
/***************************************************************************
 Interactive North Point
                                 A QGIS plugin
 Interactive compass rotation tool
                              -------------------
        begin                : 2026-06-19
        copyright            : (C) 2026 by Alister Hood
        email                : alister.hood@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""


from qgis.PyQt.QtWidgets import QWidget, QFrame, QVBoxLayout, QToolButton, QMenu, QAction
from qgis.PyQt.QtCore import Qt, QEvent, QRectF, QObject, QSize, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QPainter, QIcon
from qgis.PyQt.QtSvg import QSvgRenderer
from qgis.core import QgsProject
import math
import os

plugin_dir = os.path.dirname(__file__)
SNAP_OPTIONS = [1, 5.0, 10.0, 15.0, 22.5, 30.0, 45.0, 90.0]

try:
    QtLeftButton = Qt.MouseButton.LeftButton #QT6
    QtRightButton = Qt.MouseButton.RightButton
except AttributeError:
    QtLeftButton = Qt.LeftButton #QT5
    QtRightButton = Qt.RightButton


class SvgCompassDial(QWidget):
    valueChanged = pyqtSignal(float)

    def __init__(self, canvas, parent=None):
        super().__init__(parent)

        self.canvas = canvas
        self.value = 0.0   # ✅ float instead of int
        self.snap_origin = None
        self.snap_increment = 22.5

        # Pre-compile the SVG bytes once; QSvgRenderer reuses them on every
        # paintEvent without re-parsing.
        svg_path = os.path.join(plugin_dir, "north-point.svg")
        self._renderer = QSvgRenderer(svg_path)

    # ------------------------------------------------------------- #
    # Painting
    # ------------------------------------------------------------- #

    def paintEvent(self, event):
        painter = QPainter(self)

        try:
            hint = QPainter.RenderHint.Antialiasing # QT6
        except AttributeError:
            hint = QPainter.Antialiasing # QT5

        painter.setRenderHint(hint)

        w, h = self.width(), self.height()
        side = min(w, h)

        painter.translate(w / 2, h / 2)

        # ✅ use float value directly
        painter.rotate((-self.value) % 360)

        painter.translate(-side / 2, -side / 2)

        self._renderer.render(painter, QRectF(0, 0, side, side))
        painter.end()

    # ------------------------------------------------------------- #
    # Input handling
    # ------------------------------------------------------------- #

    def mousePressEvent(self, event):
        if event.button() == QtRightButton:
            self.setValue(0.0)
            event.accept()

        if event.button() == QtLeftButton:
            modifiers = event.modifiers()  # ✅ FIX: define it

            # ✅ capture origin BEFORE any movement happens
            if modifiers & Qt.ShiftModifier:
                self.snap_origin = self.value
            else:
                self.snap_origin = None

            # now apply movement
            self._set_value_from_pos(event.pos(), modifiers)
            event.accept()

        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        if event.buttons() & QtLeftButton:
            self._set_value_from_pos(event.pos(), event.modifiers())
            event.accept()
        else:
            event.ignore()

    def mouseReleaseEvent(self, event):
        if event.button() in (QtLeftButton, QtRightButton):
            self.snap_origin = None
            self.canvas.refresh() # actually re-render rather than just rotating the already rendered map
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------- #
    # Value handling (float)
    # ------------------------------------------------------------- #

    def setValue(self, v):
        v = v % 360.0
        if abs(self.value - v) < 0.001:
            return

        self.value = v
        self.valueChanged.emit(self.value)
        self.update()

    def _set_value_from_pos(self, pos, modifiers):
        cx = self.width() / 2
        cy = self.height() / 2

        dx = pos.x() - cx
        dy = cy - pos.y()

        angle = math.degrees(math.atan2(dx, dy)) % 360

        value = (-angle) % 360

        # ✅ relative snapping (float)
        if modifiers & Qt.ShiftModifier and self.snap_origin is not None:

            snap = self.snap_increment

            delta = value - self.snap_origin

            # keep delta continuous across 0°
            delta = (delta + 180) % 360 - 180

            snapped_delta = round(delta / snap) * snap

            value = (self.snap_origin + snapped_delta) % 360

        self.setValue(value)

class InteractiveNorthPlugin(QObject):  # Inherit QObject to support installEventFilter
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.canvas = self.iface.mapCanvas()
        self.snap_increment = 15  # default

        # References to our UI elements
        self.container = None
        self.dial = None
        self.status_button = None

    def initGui(self):
        """Called when QGIS initializes or the plugin is turned on."""
        # 1. Create the toggle button for the status bar

        self.status_button = QToolButton()
        self.status_button.setCheckable(True)
        self.status_button.setEnabled(False)
        self.status_button.setAutoRaise(True)  # toolbar-style

        # ✅ Load your SVG
        icon_path = os.path.join(plugin_dir, "icon.svg")
        self.status_button.setIcon(QIcon(icon_path))

        self.status_button.setToolTip("Toggle the interactive north point / Right-click to select snapping mode increment")
        self.status_button.toggled.connect(self.toggle_widget)
        
        # Implement context menu to configure snapping mode snap increment
        self.status_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.status_button.customContextMenuRequested.connect(self.show_snap_menu)

        # 2. Inject button into QGIS Status Bar
        self.iface.statusBarIface().addPermanentWidget(self.status_button)

        # 3. Connect project state signals to manage button availability safely
        QgsProject.instance().readProject.connect(self.enable_plugin_ui)
        QgsProject.instance().layersAdded.connect(self.enable_plugin_ui)
        # cleared fires for both "New Project" and closing a project.
        # We defer the check so QGIS has time to finish setting up the new
        # empty project before we decide whether to enable or disable the button.
        QgsProject.instance().cleared.connect(self._on_project_cleared)

        # 4. If a project is already open when the plugin loads, enable immediately
        if QgsProject.instance().mapLayers():
            self.enable_plugin_ui()

    def unload(self):
        """CRITICAL: Completely unloads elements from memory when plugin is disabled/removed."""
        # 1. Disconnect any global project signals
        try:
            QgsProject.instance().readProject.disconnect(self.enable_plugin_ui)
        except TypeError:
            pass
        try:
            QgsProject.instance().layersAdded.disconnect(self.enable_plugin_ui)
        except TypeError:
            pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_cleared)
        except TypeError:
            pass
        try:
            QgsProject.instance().cleared.disconnect(self.disable_plugin_ui)
        except TypeError:
            pass
        # 2. Tear down the map widget if it is actively running
        self.destroy_widget()

        # 3. Strip the button away from the QGIS status bar frame
        if self.status_button:
            self.iface.statusBarIface().removeWidget(self.status_button)
            self.status_button.deleteLater()
            self.status_button = None

    def _on_project_cleared(self):
        """Called when the project is cleared (new project or project closed).
        Disables the widget immediately, then defers a check: if QGIS created
        a new empty project the button stays disabled until a layer is added.
        """
        self.disable_plugin_ui()
        # A short defer lets QGIS finish any post-clear setup before we check.
        QTimer.singleShot(0, self._check_enable_after_clear)

    def _check_enable_after_clear(self):
        """Re-enables the button if a new project already has layers."""
        if QgsProject.instance().mapLayers():
            self.enable_plugin_ui()

    def enable_plugin_ui(self, *args):
        """Enables the toggle button once a project structure exists."""
        if self.status_button:
            self.status_button.setEnabled(True)

    def disable_plugin_ui(self):
        """Disables button and hides widget when dropping back to empty welcome screens."""
        if self.status_button:
            self.status_button.setChecked(False)
            self.status_button.setEnabled(False)
        self.destroy_widget()

    def toggle_widget(self, checked):
        """Handles turning the on-canvas widget display on or off."""
        if checked:
            self.build_widget()
        else:
            self.destroy_widget()

    def build_widget(self):
        """Instantiates and anchors our custom UI onto the active map frame."""
        # Guard: treat an externally-destroyed container as absent
        if self.container and self.container.isVisible():
            return

        """ AI suggests it would be safer to add this (if in future QGIS replaces the canvas object)"""
        # self.canvas = self.iface.mapCanvas()
        self.container = QFrame(self.canvas)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.dial = SvgCompassDial(self.canvas, self.container)
        self.dial.snap_increment = self.snap_increment
        self.dial.setToolTip("Rotate map / Right-click to reset to 0° / Hold shift to rotate by regular increment")
        self.dial.setFixedSize(100, 100)
        """ fails in QT6 build, but do we need it? """
        # self.dial.setAttribute(Qt.WA_TranslucentBackground)
        self.dial.setAutoFillBackground(False)

        # Initial value sync
        current_rotation = self.canvas.rotation()
        self.dial.setValue((-current_rotation) % 360.0)
        layout.addWidget(self.dial)

        # Link signals
        self.dial.valueChanged.connect(self.rotate_map)
        self.canvas.rotationChanged.connect(self.update_dial_from_canvas)

        # Use an event filter to respond to canvas resizes instead of
        # monkey-patching resizeEvent on the shared canvas instance.
        self.canvas.installEventFilter(self)

        # Show before positioning so Qt has finalised geometry
        self.container.adjustSize()
        self.container.show()
        self.position_widget()

    def destroy_widget(self):
        """Cleans up the floating panel elements without touching other core components."""
        if self.container:
            # Disconnect canvas signals first to prevent ghost update callbacks
            try:
                self.canvas.rotationChanged.disconnect(self.update_dial_from_canvas)
            except TypeError:
                pass

            # Remove the event filter we installed in build_widget
            self.canvas.removeEventFilter(self)

            self.container.hide()
            self.container.deleteLater()
            self.container = None
            self.dial = None

    def show_snap_menu(self, pos):
        menu = QMenu(self.status_button)

        info = QAction("Rotation increment for snap mode (enabled by holding shift)", menu)
        info.setEnabled(False)
        menu.addAction(info)

        menu.addSeparator()

        for angle in SNAP_OPTIONS:
            if angle == 1:
                label = "1° (snap to nearest whole degree)"
            else:
                label = f"{angle}°"

            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(angle == self.snap_increment)

            action.triggered.connect(lambda checked, a=angle: self.set_snap_increment(a))
            menu.addAction(action)

        menu.exec_(self.status_button.mapToGlobal(pos))

    def set_snap_increment(self, value):
        self.snap_increment = value
        if self.dial:
            self.dial.snap_increment = value

    # ------------------------------------------------------------------ #
    # QObject event filter — handles canvas resize without monkey-patching #
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        """Intercept resize events on the map canvas to reposition our widget."""
        try:
            resize_event = QEvent.Type.Resize #QT6
        except AttributeError:
            resize_event = QEvent.Resize #QT5

        if obj is self.canvas and event.type() == resize_event:

            self.position_widget()
        return False  # Always let Qt continue normal event processing

    # ------------------------------------------------------------------ #
    # Rotation helpers                                                      #
    # ------------------------------------------------------------------ #

    def rotate_map(self, value):
        """Translates dial position to canvas rotation."""
        self.canvas.setRotation((-value) % 360)

    def update_dial_from_canvas(self):
        """Keeps the dial in sync when the canvas is rotated by other means."""
        if self.container and self.dial:
            self.dial.blockSignals(True)
            self.dial.setValue((-self.canvas.rotation()) % 360.0)
            self.dial.blockSignals(False)

    def position_widget(self):
        if self.container:
            self.container.move(
                self.canvas.width() - self.container.width() - 25, 25
            )

def classFactory(iface):
    """QGIS entry point — called by QGIS when the plugin is loaded."""
    return InteractiveNorthPlugin(iface)