"""Desktop integration checks. Skipped when no graphical display is available."""

from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from rotodraw import RotoDrawApp


class DesktopTests(unittest.TestCase):
    def setUp(self):
        try:
            from tkinterdnd2 import TkinterDnD
            self.root = TkinterDnD.Tk()
        except (tk.TclError, RuntimeError, ImportError) as exc:
            self.skipTest(f"Desktop display or drag-and-drop extension unavailable: {exc}")
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.input = self.folder / "my artwork café.png"
        Image.new("RGBA", (256, 128), (200, 40, 60, 120)).save(self.input)
        self.app = RotoDrawApp(self.root)
        self.root.update()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        elif hasattr(self, "root"):
            self.root.destroy()
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def wait_for_work(self):
        deadline = time.monotonic() + 5
        while self.app._busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.app._busy, "Background image operation did not finish")
        self.root.update()

    def test_file_chooser_loads_and_enables_save(self):
        with patch("rotodraw.filedialog.askopenfilename", return_value=str(self.input)):
            self.app.choose_image()
        self.wait_for_work()
        self.assertEqual(self.app.document.source_path, self.input)
        self.assertNotIn("disabled", self.app.save_button.state())
        self.assertEqual(self.app.color_limit.get(), "30")
        self.assertIsNotNone(self.app._photo)

    def test_drop_callback_loads_a_path_with_spaces_and_unicode(self):
        data = self.root.tk.call("list", str(self.input))
        self.assertEqual(self.app._on_drop(SimpleNamespace(data=data)), "copy")
        self.wait_for_work()
        self.assertEqual(self.app.document.source_path, self.input)
        self.assertTrue(self.root.tk.call("package", "present", "tkdnd"))

    def test_multiple_file_drop_is_rejected(self):
        data = self.root.tk.call("list", str(self.input), str(self.input))
        with patch("rotodraw.messagebox.showinfo") as dialog:
            self.assertEqual(self.app._on_drop(SimpleNamespace(data=data)), "refuse_drop")
            dialog.assert_called_once()
        self.assertIsNone(self.app.document)

    def test_save_button_exports_requested_color_limit(self):
        self.app.open_image(self.input)
        self.wait_for_work()
        self.app.color_limit.set("12")
        output = self.folder / "working.png"
        with patch("rotodraw.filedialog.asksaveasfilename", return_value=str(output)):
            self.app.save_image()
        self.wait_for_work()
        self.assertTrue(output.is_file())
        with Image.open(output) as saved:
            self.assertIn('"color_limit": 12', saved.info["rotodraw.settings"])

    def test_invalid_pencil_limit_prevents_save(self):
        self.app.open_image(self.input)
        self.wait_for_work()
        self.app.color_limit.set("31")
        with patch("rotodraw.messagebox.showerror") as error:
            with patch("rotodraw.filedialog.asksaveasfilename") as chooser:
                self.app.save_image()
                error.assert_called_once()
                chooser.assert_not_called()

    def test_failed_load_keeps_previous_image(self):
        self.app.open_image(self.input)
        self.wait_for_work()
        previous = self.app.document
        with patch("rotodraw.messagebox.showerror") as error:
            self.app.open_image(self.folder / "missing.png")
            self.wait_for_work()
            error.assert_called_once()
        self.assertIs(self.app.document, previous)
        self.assertNotIn("disabled", self.app.save_button.state())

    def test_save_control_remains_visible_at_minimum_window_size(self):
        self.root.geometry("900x650")
        self.app.open_image(self.input)
        self.wait_for_work()
        self.root.update_idletasks()
        button_bottom = self.app.save_button.winfo_rooty() + self.app.save_button.winfo_height()
        window_bottom = self.root.winfo_rooty() + self.root.winfo_height()
        self.assertLess(button_bottom, window_bottom)
        self.assertGreater(self.app.canvas.winfo_width(), 300)


if __name__ == "__main__":
    unittest.main()
