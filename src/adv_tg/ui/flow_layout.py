"""Layout that places its items left to right and wraps to a new row when they don't fit.

Used by the top bar: on high-DPI screens with display scaling (e.g. 1920x1080 at 150 %, only
1280 logical pixels wide) a single row of controls doesn't fit, and a QHBoxLayout then lets
the widgets overlap.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    """Rows of items, wrapped to the available width (height depends on width)."""

    def __init__(self, parent: QWidget | None = None, h_spacing: int = 10, v_spacing: int = 6):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing

    # --- QLayout interface -------------------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 (Qt API)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        """One row with everything."""
        m = self.contentsMargins()
        hints = [self._item_size(item) for item in self._items]
        width = sum(h.width() for h in hints) + self._h_spacing * max(0, len(hints) - 1)
        height = max((h.height() for h in hints), default=0)
        return QSize(width + m.left() + m.right(), height + m.top() + m.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802
        """The widest single item (everything else can wrap)."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    @staticmethod
    def _item_size(item: QLayoutItem) -> QSize:
        """Minimum width (like a squeezed QHBoxLayout, so as much as possible fits in a row)
        and preferred height, kept within the item's maximum."""
        size = QSize(item.minimumSize().width(), item.sizeHint().height())
        return size.expandedTo(item.minimumSize()).boundedTo(item.maximumSize())

    # --- Placement ---------------------------------------------------------------------
    def _do_layout(self, rect: QRect, *, apply: bool) -> int:
        """Place the items (``apply``) and return the height they need for ``rect.width()``.

        Items of a row share its height and are centred vertically in it, so controls of
        different heights (a checkbox next to combo boxes) stay aligned.
        """
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        rows: list[list[tuple[QLayoutItem, QSize]]] = [[]]
        x = 0
        for item in self._items:
            hint = self._item_size(item)
            if rows[-1] and x + hint.width() > area.width():
                rows.append([])
                x = 0
            rows[-1].append((item, hint))
            x += hint.width() + self._h_spacing

        y = area.y()
        for row in rows:
            row_h = max((hint.height() for _, hint in row), default=0)
            if apply:
                x = area.x()
                for item, hint in row:
                    top = y + (row_h - hint.height()) // 2
                    item.setGeometry(QRect(QPoint(x, top), hint))
                    x += hint.width() + self._h_spacing
            y += row_h + self._v_spacing
        used = y - self._v_spacing - area.y() if rows[0] else 0
        return used + m.top() + m.bottom()
