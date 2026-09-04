import unittest

from multichannel_layout.models import BoardTransform
from multichannel_layout.transforms import (
    calculate_transform,
    choose_consensus_transform,
    transform_from_origins,
    transform_point,
)


class TransformTests(unittest.TestCase):
    def test_calculated_transform_maps_source_to_target(self):
        transform = calculate_transform(
            source_position=(2_000_000, 1_000_000),
            source_rotation=0,
            target_position=(12_000_000, 9_000_000),
            target_rotation=90,
            center=(1_000_000, 1_000_000),
        )

        self.assertEqual(
            transform_point((2_000_000, 1_000_000), transform),
            (12_000_000, 9_000_000),
        )
        self.assertEqual(transform.rotation, 90)

    def test_consensus_uses_largest_bucket(self):
        transforms = [
            BoardTransform((0, 0), (1_000_000, 2_000_000), 0),
            BoardTransform((0, 0), (1_100_000, 1_900_000), 0),
            BoardTransform((0, 0), (9_000_000, 9_000_000), 90),
        ]

        result = choose_consensus_transform(transforms, 500_000)

        self.assertEqual(result.translation, (1_050_000, 1_950_000))
        self.assertEqual(result.rotation, 0)

    def test_transform_from_origins_includes_origin_rotation(self):
        transform = transform_from_origins(
            (1_000_000, 2_000_000, 90),
            (11_000_000, 12_000_000, 180),
        )

        self.assertEqual(
            transform_point((2_000_000, 2_000_000), transform),
            (11_000_000, 11_000_000),
        )


if __name__ == "__main__":
    unittest.main()
