from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from talking_sprite import compose_pair


class TalkingSpriteTests(unittest.TestCase):
    def test_model_redraw_is_restricted_to_mouth_and_alpha_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = np.full((80, 80, 4), (120, 170, 220, 255), dtype=np.uint8)
            source[:10, :, 3] = 0
            Image.fromarray(source).save(root / "source.png")
            Image.new("RGBA", (80, 80), (240, 30, 40, 100)).save(root / "generated.png")
            value = compose_pair(root / "source.png", root / "generated.png", root / "talk.png", [20, 30, 60, 60])
            with Image.open(root / "talk.png") as image:
                result = np.array(image)
            outside = np.ones((80, 80), dtype=bool)
            outside[30:60, 20:60] = False
            np.testing.assert_array_equal(result[outside], source[outside])
            np.testing.assert_array_equal(result[:, :, 3], source[:, :, 3])
            self.assertGreater(value["changed_pixels"], 20)

    def test_unchanged_frame_is_not_exported_as_a_talking_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new("RGBA", (80, 80), (100, 120, 140, 255)).save(root / "source.png")
            with self.assertRaisesRegex(ValueError, "did not visibly change"):
                compose_pair(root / "source.png", root / "source.png", root / "talk.png", [20, 30, 60, 60])
            self.assertFalse((root / "talk.png").exists())


if __name__ == "__main__":
    unittest.main()
