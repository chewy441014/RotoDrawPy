from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image, ImageCms, ImageDraw

from image_loader import ImageLoadError, load_and_normalize, save_normalized_png
from rotodraw import dropped_paths, make_preview, parse_color_limit


class ImageLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write_image(self, image, name="art.png", **kwargs):
        path = self.folder / name
        image.save(path, **kwargs)
        return path

    def test_downscales_landscape_and_portrait_without_upscaling(self):
        for source_size, expected in (
            ((4096, 1024), (2048, 512)),
            ((1024, 4096), (512, 2048)),
            ((2048, 2048), (2048, 2048)),
            ((99, 51), (99, 51)),
            ((5000, 1), (2048, 1)),
        ):
            with self.subTest(size=source_size):
                path = self.write_image(Image.new("RGB", source_size, "red"))
                result = load_and_normalize(path)
                self.assertEqual(result.image.size, expected)
                self.assertEqual(result.image.mode, "RGBA")

    def test_corrects_exif_orientation_before_resizing(self):
        image = Image.new("RGB", (40, 20), "white")
        ImageDraw.Draw(image).rectangle((0, 0, 9, 9), fill="red")
        exif = Image.Exif()
        exif[274] = 6  # Camera says rotate 90 degrees clockwise.
        path = self.write_image(image, "rotated.jpg", exif=exif, quality=100, subsampling=0)
        before = hashlib.sha256(path.read_bytes()).digest()
        result = load_and_normalize(path)
        self.assertEqual(result.source_size, (40, 20))
        self.assertEqual(result.oriented_size, (20, 40))
        self.assertTrue(result.orientation_corrected)
        red, green, blue, alpha = result.image.getpixel((15, 5))
        self.assertGreater(red, 240)
        self.assertLess(green, 10)
        self.assertLess(blue, 10)
        self.assertEqual(alpha, 255)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_preserves_enclosed_transparent_gap_and_partial_alpha(self):
        image = Image.new("RGBA", (80, 80), (255, 0, 0, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 60, 60), fill=(0, 0, 0, 0))
        image.putpixel((0, 0), (20, 100, 200, 90))
        result = load_and_normalize(self.write_image(image))
        self.assertTrue(result.has_transparency)
        self.assertEqual(result.image.getpixel((40, 40))[3], 0)
        self.assertEqual(result.image.getpixel((0, 0)), (20, 100, 200, 90))
        self.assertEqual(result.image.getpixel((10, 10)), (255, 0, 0, 255))

    def test_preserves_transparency_when_resizing(self):
        image = Image.new("RGBA", (4096, 1024), "red")
        ImageDraw.Draw(image).rectangle((1600, 200, 2400, 800), fill=(0, 0, 0, 0))
        result = load_and_normalize(self.write_image(image))
        self.assertEqual(result.image.size, (2048, 512))
        self.assertEqual(result.image.getpixel((1000, 250))[3], 0)

    def test_indexed_palette_keeps_transparency_table(self):
        image = Image.new("P", (10, 2))
        image.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
        image.putdata([0] * 5 + [1] * 5 + [0] * 5 + [1] * 5)
        path = self.write_image(image, transparency=bytes([0, 128] + [255] * 254))
        result = load_and_normalize(path)
        self.assertEqual(result.image.getpixel((0, 0)), (255, 0, 0, 0))
        self.assertEqual(result.image.getpixel((9, 0)), (0, 255, 0, 128))

    def test_grayscale_alpha_keeps_both_channels(self):
        result = load_and_normalize(self.write_image(Image.new("LA", (8, 8), (45, 90))))
        self.assertEqual(result.image.getpixel((0, 0)), (45, 45, 45, 90))

    def test_embedded_srgb_does_not_change_rgb_or_alpha(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        path = self.write_image(Image.new("RGBA", (4, 4), (27, 100, 211, 140)), icc_profile=profile)
        result = load_and_normalize(path)
        self.assertEqual(result.image.getpixel((0, 0)), (27, 100, 211, 140))
        self.assertIn("converted", result.color_handling)

    def test_lab_profile_is_applied_to_native_color_channels(self):
        srgb = ImageCms.createProfile("sRGB")
        lab = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
        original = Image.new("RGB", (4, 4), (30, 160, 70))
        lab_image = ImageCms.profileToProfile(original, srgb, lab, outputMode="LAB")
        path = self.write_image(lab_image, "lab.tiff", icc_profile=lab.tobytes())
        result = load_and_normalize(path)
        for actual, expected in zip(result.image.getpixel((0, 0)), (30, 160, 70, 255)):
            self.assertLessEqual(abs(actual - expected), 4)

    def test_untagged_cmyk_is_converted_with_an_explicit_note(self):
        path = self.write_image(Image.new("CMYK", (4, 4), (0, 255, 255, 0)), "cmyk.jpg")
        result = load_and_normalize(path)
        self.assertGreater(result.image.getpixel((0, 0))[0], 240)
        self.assertIn("approximate", result.color_handling)
        self.assertTrue(result.notes)

    def test_bad_embedded_profile_is_reported(self):
        path = self.write_image(Image.new("RGB", (4, 4)), icc_profile=b"not an ICC profile")
        with self.assertRaisesRegex(ImageLoadError, "color profile"):
            load_and_normalize(path)

    def test_multiframe_input_uses_first_frame_with_note(self):
        path = self.folder / "animated.gif"
        Image.new("RGB", (5, 5), "red").save(
            path, save_all=True, append_images=[Image.new("RGB", (5, 5), "blue")], duration=100
        )
        result = load_and_normalize(path)
        self.assertEqual(result.frame_count, 2)
        self.assertIn("frame/page 1", result.notes[0])
        self.assertEqual(result.image.getpixel((0, 0)), (255, 0, 0, 255))

    def test_source_pixel_limit_is_checked(self):
        path = self.write_image(Image.new("RGB", (11, 10)))
        with self.assertRaisesRegex(ImageLoadError, "exceeds 100"):
            load_and_normalize(path, max_source_pixels=100)

    def test_pillow_decompression_limit_is_reported(self):
        path = self.write_image(Image.new("RGB", (10, 10)))
        with patch.object(Image, "MAX_IMAGE_PIXELS", 10):
            with self.assertRaisesRegex(ImageLoadError, "too large"):
                load_and_normalize(path)

    def test_high_bit_depth_is_rejected_without_silent_clipping(self):
        path = self.write_image(Image.new("I;16", (4, 4), 60000))
        with self.assertRaisesRegex(ImageLoadError, "8-bit"):
            load_and_normalize(path)

    def test_invalid_files_and_directories_have_readable_errors(self):
        invalid = self.folder / "broken.png"
        invalid.write_bytes(b"not an image")
        for path in (invalid, self.folder / "missing.png", self.folder):
            with self.subTest(path=path):
                with self.assertRaises(ImageLoadError):
                    load_and_normalize(path)

    def test_unicode_and_spaces_in_filename(self):
        path = self.write_image(Image.new("RGB", (5, 5)), "my café artwork.png")
        self.assertEqual(load_and_normalize(path).source_path, path.resolve())

    def test_export_is_rgba_srgb_and_contains_requested_color_limit(self):
        path = self.write_image(Image.new("RGBA", (8, 8), (15, 30, 45, 80)))
        document = load_and_normalize(path)
        output = save_normalized_png(document, self.folder / "working.png", color_limit=12)
        with Image.open(output) as saved:
            self.assertEqual(saved.mode, "RGBA")
            self.assertEqual(saved.getpixel((0, 0)), (15, 30, 45, 80))
            self.assertTrue(saved.info["icc_profile"])
            settings = json.loads(saved.info["rotodraw.settings"])
            self.assertEqual(settings["color_limit"], 12)
            self.assertEqual(settings["stage"], "image_normalization")
            self.assertNotIn("exif", saved.info)

    def test_cannot_export_onto_original(self):
        path = self.write_image(Image.new("RGB", (8, 8), "red"))
        document = load_and_normalize(path)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "original"):
            save_normalized_png(document, path)
        self.assertEqual(path.read_bytes(), before)

    def test_export_failure_preserves_existing_output_and_cleans_temp_file(self):
        path = self.write_image(Image.new("RGB", (8, 8)))
        document = load_and_normalize(path)
        target = self.folder / "working.png"
        target.write_bytes(b"previous output")
        with patch.object(Image.Image, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_normalized_png(document, target)
        self.assertEqual(target.read_bytes(), b"previous output")
        self.assertFalse(list(self.folder.glob(".working-*.png")))

    def test_preview_background_does_not_modify_working_pixels(self):
        image = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
        before = image.tobytes()
        preview = make_preview(image, (100, 100), paper=True)
        self.assertEqual(preview.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(image.tobytes(), before)

    def test_color_limit_accepts_only_one_to_thirty(self):
        self.assertEqual(parse_color_limit(" 12 "), 12)
        for value in ("", "0", "31", "2.5", "red"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_color_limit(value)

    def test_drop_data_uses_tcl_list_rules(self):
        interpreter = tk.Tcl()
        paths = dropped_paths(interpreter, "{C:/My art/café {spring}.png} {C:/Second image.png}")
        self.assertEqual(paths, ("C:/My art/café {spring}.png", "C:/Second image.png"))


if __name__ == "__main__":
    unittest.main()
