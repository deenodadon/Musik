"""Audio-reactive level history, measured by mpv's astats filter."""
import math

from textual.widgets import Static


def rms_level(metadata):
    try:
        db = float(metadata.get("lavfi.astats.Overall.RMS_level", "-inf"))
    except (ValueError, TypeError):
        return 0.0
    return max(0.0, min(1.0, (db + 60) / 60)) if math.isfinite(db) else 0.0


def bars(levels, height=5):
    """One column per recent RMS sample; silence produces no filled bars."""
    heights = [round(max(0., min(1., value)) * height) for value in levels]
    return "\n".join(" ".join("█" if h >= row else " " for h in heights)
                     for row in range(height, 0, -1))


class Visualizer(Static):
    DEFAULT_CSS = "Visualizer { height: 7; margin: 0 2; padding: 0 1; border: round #435674; color: #66d9bb; }"

    def show_levels(self, levels):
        count = max(1, min(32, (self.content_size.width + 1) // 2))
        self.update(bars(levels[-count:], height=max(1, min(5, self.content_size.height))))
