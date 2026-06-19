from qgis.PyQt.QtWidgets import QDial, QFrame, QVBoxLayout, QPushButton
from qgis.PyQt.QtCore import Qt, QEvent, QByteArray, QRectF, QObject, QSize, QTimer
from qgis.PyQt.QtGui import QPainter, QIcon
from qgis.PyQt.QtSvg import QSvgRenderer
from qgis.core import QgsProject
from qgis.utils import iface
import math
import os

plugin_dir = os.path.dirname(__file__)

class SvgCompassDial(QDial):
    """
    A QDial subclass that discards Qt's default knob rendering and instead
    paints an SVG compass rose that rotates to match the current dial value.

    Value 0   → north arrow points up   (map is north-up)
    Value 90  → north arrow points left (map rotated 90° clockwise)
    etc.

    The SVG is defined inline so the class has no external file dependency.
    It consists of:
      - A white background disc
      - A two-tone needle: red (north) and dark-grey (south)
      - A small centre boss
      - A bold "N" label that rotates with the needle
    """

    # Inline SVG — authored in a 100×100 viewBox so it scales cleanly to
    # whatever fixed size the parent sets via setFixedSize().
    _SVG_TEMPLATE = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <!-- Drop shadow -->
  <circle cx="50" cy="51" r="44" fill="rgba(0,0,0,0.08)"/>
  <!-- Background disc -->
  <circle cx="50" cy="50" r="44" fill="white"/>
  <!-- Bearing ring -->
  <circle cx="50" cy="50" r="44" fill="none" stroke="#cccccc" stroke-width="1.5"/>

  <!-- Compass needle, centred on origin for clean rotation -->
  <!-- North half: red -->
  <polygon points="50,16 55,50 50,46 45,50"
           fill="#cc2222"/>
  <!-- South half: dark grey -->
  <polygon points="50,84 55,50 50,54 45,50"
           fill="#444444"/>

  <!-- Centre boss -->
  <circle cx="50" cy="50" r="4" fill="#333333"/>
  <circle cx="50" cy="50" r="2" fill="white"/>

  <!-- "N" label, positioned above needle tip and rotated with it -->
  <text x="50" y="16"
        text-anchor="middle"
        dominant-baseline="auto"
        font-family="sans-serif"
        font-size="9"
        font-weight="bold"
        fill="#cc2222">N</text>
</svg>"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Pre-compile the SVG bytes once; QSvgRenderer reuses them on every
        # paintEvent without re-parsing.
        self._renderer = QSvgRenderer(QByteArray(self._SVG_TEMPLATE.encode()))

    def paintEvent(self, event):
        """
        Completely replaces QDial's native rendering.

        Rotation logic
        --------------
        dial value 0   → needle points up   → rotate(0°)
        dial value 90  → needle points right → rotate(90°)

        Qt's painter rotation is clockwise, matching compass bearings, so we
        simply use the dial value directly as the rotation angle.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        side = min(w, h)

        # Centre the drawing area and make it square
        painter.translate(w / 2, h / 2)
        painter.rotate(self.value())          # rotate by bearing (0–359°)
        painter.translate(-side / 2, -side / 2)

        # Render the SVG into the square bounding box
        self._renderer.render(painter, QRectF(0, 0, side, side))

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            # ✅ Reset to north
            self.setValue(0)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            self._is_interacting = True
            self._set_value_from_pos(event.pos())
            event.accept()
        else:
            super().mousePressEvent(event)


    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._set_value_from_pos(event.pos())
            event.accept()
        else:
            super().mouseMoveEvent(event)


    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.RightButton):
            self._is_interacting = False
            # actually re-render rather than just rotating the already rendered map
            iface.mapCanvas().refresh()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _set_value_from_pos(self, pos):
        cx = self.width() / 2
        cy = self.height() / 2

        dx = pos.x() - cx
        dy = cy - pos.y()  # inverted Y axis (Qt screen coords)

        angle = math.degrees(math.atan2(dx, dy))

        if angle < 0:
            angle += 360

        self.setValue(int(angle))


class InteractiveNorthPlugin(QObject):  # Inherit QObject to support installEventFilter
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.canvas = self.iface.mapCanvas()

        # References to our UI elements
        self.container = None
        self.dial = None
        self.status_button = None

    def initGui(self):
        """Called when QGIS initializes or the plugin is turned on."""
        # 1. Create the toggle button for the status bar

        self.status_button = QPushButton()
        self.status_button.setCheckable(True)
        self.status_button.setEnabled(False)

        # ✅ Load your SVG
        icon_path = os.path.join(plugin_dir, "icon.svg")
        self.status_button.setIcon(QIcon(icon_path))

        # ✅ Make it a clean icon button
        self.status_button.setIconSize(QSize(18, 18))   # tweak if needed
        self.status_button.setFixedSize(24, 24)         # nice compact square

        self.status_button.setToolTip("Toggle the interactive north point")
        self.status_button.toggled.connect(self.toggle_widget)

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

        self.container = QFrame(self.canvas)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.dial = SvgCompassDial()
        self.dial.setToolTip("Rotate map / Right-click to reset north")
        self.dial.setMinimum(0)
        self.dial.setMaximum(359)
        self.dial.setWrapping(True)
        self.dial.setFixedSize(100, 100)
        self.dial.setAttribute(Qt.WA_TranslucentBackground)
        self.dial.setAutoFillBackground(False)

        # Initial value sync
        current_rotation = round(self.canvas.rotation())
        self.dial.setValue((360 - current_rotation) % 360)
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

    # ------------------------------------------------------------------ #
    # QObject event filter — handles canvas resize without monkey-patching #
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        """Intercept resize events on the map canvas to reposition our widget."""
        if obj is self.canvas and event.type() == QEvent.Resize:
            self.position_widget()
        return False  # Always let Qt continue normal event processing

    # ------------------------------------------------------------------ #
    # Rotation helpers                                                      #
    # ------------------------------------------------------------------ #

    def rotate_map(self, value):
        """Translates dial position to canvas rotation."""
        self.canvas.setRotation((360 - value) % 360)

    def update_dial_from_canvas(self):
        """Keeps the dial in sync when the canvas is rotated by other means."""
        if self.container and self.dial:
            self.dial.blockSignals(True)
            c_rot = round(self.canvas.rotation())
            self.dial.setValue((360 - c_rot) % 360)
            self.dial.blockSignals(False)

    def position_widget(self):
        if self.container:
            self.container.move(
                self.canvas.width() - self.container.width() - 25, 25
            )


def classFactory(iface):
    """QGIS entry point — called by QGIS when the plugin is loaded."""
    return InteractiveNorthPlugin(iface)