import math
import unittest

from meninges_annotation.calibration import Calibration, nice_scale_length


class CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calibration = Calibration(
            calibration_id="synthetic-test-only",
            x_um_per_px=2.0,
            y_um_per_px=3.0,
            display_scale=4,
            display_x_um_per_px=0.5,
            display_y_um_per_px=0.75,
        )

    def test_axis_calibration(self) -> None:
        self.assertAlmostEqual(
            self.calibration.distance_um((0, 0), (1, 0)), 2.0, places=9
        )
        self.assertAlmostEqual(
            self.calibration.distance_um((0, 0), (0, 1)),
            3.0,
            places=9,
        )

    def test_source_and_display_measurements_are_equivalent(self) -> None:
        p1 = (13.25, 21.5)
        p2 = (27.75, 49.125)
        source_distance = self.calibration.distance_um(p1, p2)
        display_dx = (p2[0] - p1[0]) * self.calibration.display_scale
        display_dy = (p2[1] - p1[1]) * self.calibration.display_scale
        display_distance = math.hypot(
            display_dx * self.calibration.display_x_um_per_px,
            display_dy * self.calibration.display_y_um_per_px,
        )
        self.assertAlmostEqual(source_distance, display_distance, places=9)

    def test_full_image_physical_extent(self) -> None:
        self.assertAlmostEqual(8 * self.calibration.x_um_per_px, 16.0)
        self.assertAlmostEqual(6 * self.calibration.y_um_per_px, 18.0)

    def test_nice_scale_length(self) -> None:
        self.assertEqual(nice_scale_length(18.0, 0.2), 2.0)
        self.assertEqual(nice_scale_length(16.0, 0.2), 2.0)


if __name__ == "__main__":
    unittest.main()
