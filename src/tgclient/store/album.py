"""Albums (messages sharing media_album_id) laid out as a grid in one bubble. Qt-free.

Rows of 1–4 items; within a row items keep their aspect ratio and share one height, so the
row fills the width exactly. Images are cover-cropped, so clamped heights stay fine.
"""

from __future__ import annotations

from dataclasses import dataclass

# How many items go into each row, top to bottom, for an album of n (Telegram allows 10).
_ROWS = {1: [1], 2: [2], 3: [1, 2], 4: [2, 2], 5: [2, 3], 6: [3, 3], 7: [2, 2, 3],
         8: [2, 3, 3], 9: [3, 3, 3], 10: [3, 3, 4]}
MIN_ROW = 70
MAX_ROW = 320


@dataclass(frozen=True)
class Cell:
    x: int
    y: int
    width: int
    height: int


def album_layout(sizes: list[tuple[int, int]], width: int, spacing: int = 2) -> list[Cell]:
    """Cells for items of these (width, height), in order, inside a box `width` wide."""
    if not sizes:
        return []
    ratios = [w / h if w > 0 and h > 0 else 1.0 for w, h in sizes]
    count = len(ratios)
    rows = _ROWS.get(count) or [3] * (count // 3) + ([count % 3] if count % 3 else [])
    if count == 2 and all(r > 1.4 for r in ratios):
        rows = [1, 1]  # two wide landscapes look better stacked
    cells: list[Cell] = []
    y = 0
    start = 0
    for in_row in rows:
        row = ratios[start:start + in_row]
        free = width - spacing * (len(row) - 1)
        height = round(min(MAX_ROW, max(MIN_ROW, free / sum(row))))
        x = 0
        for index, ratio in enumerate(row):
            last = index == len(row) - 1
            cell_width = width - x if last else round(free * ratio / sum(row))
            cells.append(Cell(x, y, cell_width, height))
            x += cell_width + spacing
        y += height + spacing
        start += in_row
    return cells
