"""Desktop image setup for RotoDraw. Run with: python rotodraw.py"""

from __future__ import annotations

import logging
from pathlib import Path
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from image_loader import MAX_SIDE, NormalizedImage, load_and_normalize, save_normalized_png


FILE_TYPES = [
    ("Supported images", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif"),
    ("PNG", "*.png"),
    ("JPEG", "*.jpg *.jpeg"),
    ("All files", "*.*"),
]


def parse_color_limit(value: str) -> int:
    try:
        number = int(value.strip())
    except ValueError as exc:
        raise ValueError("Choose a whole-number pencil limit from 1 to 30.") from exc
    if not 1 <= number <= 30:
        raise ValueError("Choose a whole-number pencil limit from 1 to 30.")
    return number


def dropped_paths(interpreter: tk.Misc, data: str) -> tuple[str, ...]:
    """Use Tcl list parsing: spaces, Unicode, and braces are valid in paths."""
    return tuple(interpreter.tk.splitlist(data))


def make_preview(image: Image.Image, size: tuple[int, int], paper: bool) -> Image.Image:
    """Return a display-only composite without changing the RGBA working image."""
    foreground = image.copy()
    foreground.thumbnail(size, Image.Resampling.LANCZOS)
    background = Image.new("RGBA", foreground.size, "white" if paper else "#eef0f3")
    if not paper:
        draw = ImageDraw.Draw(background)
        cell = 16
        for y in range(0, background.height, cell):
            for x in range(0, background.width, cell):
                if (x // cell + y // cell) % 2:
                    draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill="#dce1e7")
    return Image.alpha_composite(background, foreground).convert("RGB")


class RotoDrawApp:
    def __init__(self, root: tk.Tk, *, enable_drop: bool = True):
        self.root = root
        self.document: NormalizedImage | None = None
        self._results: Queue = Queue()
        self._busy = False
        self._closed = False
        self._photo: ImageTk.PhotoImage | None = None
        self._resize_job: str | None = None
        self._poll_job: str | None = None
        self.color_limit = tk.StringVar(root, value="30")
        self.background = tk.StringVar(root, value="Transparency")
        self.status = tk.StringVar(root, value="Choose an image to begin.")
        self.filename = tk.StringVar(root, value="No image selected")
        self.details = tk.StringVar(root, value="Your image details will appear here.")
        self.color_note = tk.StringVar(root, value="")
        self.preview_info = tk.StringVar(root, value="")
        self._configure_window()
        self._build_interface()
        if enable_drop:
            self._enable_drop()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-o>", lambda _event: self.choose_image())
        self._poll_job = self.root.after(80, self._poll_results)

    def _configure_window(self) -> None:
        self.root.title("RotoDraw — Image setup")
        self.root.geometry("1120x760")
        self.root.minsize(900, 650)
        self.root.configure(background="#f3f5f7")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f7")
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background="#f3f5f7", foreground="#1e293b", font=("Segoe UI", 10))
        style.configure("Card.TLabel", background="white")
        style.configure("Muted.TLabel", background="white", foreground="#58677b", font=("Segoe UI", 9))
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("Section.TLabel", background="white", font=("Segoe UI", 11, "bold"))
        style.configure("TButton", padding=(12, 9), font=("Segoe UI", 10))
        style.configure("Accent.TButton", background="#166d60", foreground="white")
        style.map("Accent.TButton", background=[("active", "#12574d"), ("disabled", "#cbd5e1")])
        style.configure("TSpinbox", padding=5, font=("Segoe UI", 11))
        style.configure("TCombobox", padding=4, font=("Segoe UI", 9))

    def _build_interface(self) -> None:
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 20))
        ttk.Label(header, text="RotoDraw", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Start with your artwork. Prepare a clean working image.").pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        sidebar_card = ttk.Frame(body, style="Card.TFrame", width=306)
        sidebar_card.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        sidebar_card.grid_propagate(False)
        sidebar_card.columnconfigure(0, weight=1)
        sidebar_card.rowconfigure(0, weight=1)
        self.sidebar_scroll = tk.Canvas(sidebar_card, background="white", highlightthickness=0)
        self.sidebar_scroll.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(sidebar_card, orient="vertical", command=self.sidebar_scroll.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.sidebar_scroll.configure(yscrollcommand=scrollbar.set)
        self.sidebar = ttk.Frame(self.sidebar_scroll, style="Card.TFrame", padding=(18, 18, 18, 4))
        sidebar_window = self.sidebar_scroll.create_window((0, 0), window=self.sidebar, anchor="nw")
        self.sidebar.bind("<Configure>", lambda _event: self.sidebar_scroll.configure(scrollregion=self.sidebar_scroll.bbox("all")))
        self.sidebar_scroll.bind("<Configure>", lambda event: self.sidebar_scroll.itemconfigure(sidebar_window, width=event.width))
        self.sidebar.columnconfigure(0, weight=1)

        ttk.Label(self.sidebar, text="1  Choose artwork", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.choose_button = ttk.Button(self.sidebar, text="Choose image…", style="Accent.TButton", command=self.choose_image)
        self.choose_button.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        self.import_hint = ttk.Label(self.sidebar, text="Or drop one image into the preview.\nPNG, JPEG, WebP, BMP, TIFF, GIF", style="Muted.TLabel", wraplength=246)
        self.import_hint.grid(row=2, column=0, sticky="w")
        ttk.Label(self.sidebar, textvariable=self.filename, style="Card.TLabel", wraplength=246).grid(row=3, column=0, sticky="w", pady=(18, 8))
        ttk.Label(self.sidebar, textvariable=self.details, style="Muted.TLabel", justify="left", wraplength=246).grid(row=4, column=0, sticky="nw")
        ttk.Separator(self.sidebar).grid(row=5, column=0, sticky="ew", pady=18)

        ttk.Label(self.sidebar, text="2  Pencil colors", style="Section.TLabel").grid(row=6, column=0, sticky="w")
        self.color_spinner = ttk.Spinbox(self.sidebar, from_=1, to=30, textvariable=self.color_limit, width=6)
        self.color_spinner.grid(row=7, column=0, sticky="w", pady=(10, 8))
        ttk.Label(self.sidebar, text="Maximum pencils: 1–30. This choice is saved for the color-extraction stage.", style="Muted.TLabel", wraplength=246).grid(row=8, column=0, sticky="w")
        ttk.Label(self.sidebar, textvariable=self.color_note, style="Muted.TLabel", wraplength=246, justify="left").grid(row=10, column=0, sticky="sw", pady=(16, 14))
        self.save_button = ttk.Button(sidebar_card, text="Save normalized PNG…", command=self.save_image, state="disabled")
        self.save_button.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(8, 18))
        for widget in (self.sidebar_scroll, self.sidebar, *self.sidebar.winfo_children()):
            if widget is not self.color_spinner:
                widget.bind("<MouseWheel>", self._scroll_sidebar)
                widget.bind("<Button-4>", lambda _event: self.sidebar_scroll.yview_scroll(-1, "units"))
                widget.bind("<Button-5>", lambda _event: self.sidebar_scroll.yview_scroll(1, "units"))

        preview = ttk.Frame(body, style="Card.TFrame", padding=12)
        preview.grid(row=0, column=1, sticky="nsew")
        preview.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        tools = ttk.Frame(preview, style="Card.TFrame")
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(tools, text="Image preview", style="Section.TLabel").pack(side="left")
        view = ttk.Combobox(tools, textvariable=self.background, values=("Transparency", "White paper"), state="readonly", width=14)
        view.pack(side="right")
        view.bind("<<ComboboxSelected>>", lambda _event: self._draw_preview())
        self.canvas = tk.Canvas(preview, background="#f8fafc", highlightthickness=1, highlightbackground="#e1e6ec", borderwidth=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self._schedule_preview)
        self.canvas.bind("<Button-1>", lambda _event: self.choose_image() if self.document is None else None)
        ttk.Label(preview, textvariable=self.preview_info, style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(14, 0))
        ttk.Label(footer, textvariable=self.status, wraplength=760).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=130)
        self.progress.pack(side="right", padx=(12, 0))

    def _scroll_sidebar(self, event) -> str:
        if event.delta:
            self.sidebar_scroll.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _enable_drop(self) -> None:
        from tkinterdnd2 import DND_FILES

        try:
            for widget in (self.root, self.canvas, self.sidebar):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
        except tk.TclError:
            logging.exception("Could not register file drop targets")
            self.import_hint.configure(text="Drag and drop is unavailable.\nUse Choose image to open your artwork.")

    def _on_drop(self, event) -> str:
        if self._busy:
            return "refuse_drop"
        try:
            paths = dropped_paths(self.root, event.data)
        except tk.TclError:
            messagebox.showerror("Could not read drop", "Use Choose image to open this file.", parent=self.root)
            return "refuse_drop"
        if len(paths) != 1:
            messagebox.showinfo("One image at a time", "Drop a single image for this project.", parent=self.root)
            return "refuse_drop"
        self.open_image(paths[0])
        return "copy"

    def choose_image(self) -> None:
        if self._busy:
            return
        filename = filedialog.askopenfilename(parent=self.root, title="Choose RotoDraw artwork", filetypes=FILE_TYPES)
        if filename:
            self.open_image(filename)

    def _start_work(self, operation: str, function, *args, **kwargs) -> None:
        self._busy = True
        self.choose_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.color_spinner.configure(state="disabled")
        self.progress.start(12)

        def run() -> None:
            try:
                payload = function(*args, **kwargs)
                self._results.put((operation, payload, None))
            except (ValueError, OSError) as exc:
                self._results.put((operation, None, exc))
            except Exception as exc:
                logging.exception("RotoDraw %s failed", operation)
                self._results.put((operation, None, exc))

        threading.Thread(target=run, name=f"rotodraw-{operation}", daemon=True).start()

    def open_image(self, filename: str | Path) -> None:
        if self._busy:
            return
        self.status.set("Loading and preparing your image…")
        self._start_work("load", load_and_normalize, filename)

    def save_image(self) -> None:
        if self._busy or self.document is None:
            return
        try:
            limit = parse_color_limit(self.color_limit.get())
        except ValueError as exc:
            messagebox.showerror("Pencil limit", str(exc), parent=self.root)
            self.color_spinner.focus_set()
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save normalized image",
            defaultextension=".png",
            initialfile=f"{self.document.source_path.stem}_normalized.png",
            filetypes=[("PNG image", "*.png")],
            confirmoverwrite=True,
        )
        if filename:
            self.status.set("Saving your normalized image…")
            self._start_work("save", save_normalized_png, self.document, filename, color_limit=limit)

    def _poll_results(self) -> None:
        if self._closed:
            return
        try:
            operation, payload, error = self._results.get_nowait()
        except Empty:
            pass
        else:
            self._busy = False
            self.progress.stop()
            self.choose_button.configure(state="normal")
            self.color_spinner.configure(state="normal")
            if error is not None:
                if operation == "save":
                    self.status.set("Save failed. Please try again.")
                else:
                    self.status.set("Image unchanged. Please try again." if self.document else "Choose another image to continue.")
                messagebox.showerror("Image could not be loaded" if operation == "load" else "Image could not be saved", str(error), parent=self.root)
            elif operation == "load":
                self._show_document(payload)
            else:
                self.status.set(f"Saved {payload.name}")
            self.save_button.configure(state="normal" if self.document else "disabled")
        if not self._closed:
            self._poll_job = self.root.after(80, self._poll_results)

    def _show_document(self, document: NormalizedImage) -> None:
        self.document = document
        self.filename.set(document.source_path.name)
        width, height = document.source_size
        working_width, working_height = document.image.size
        lines = [
            f"Source: {width:,} × {height:,} px",
            f"Working: {working_width:,} × {working_height:,} px",
            f"Format: {document.source_format} · {document.source_mode}",
            "Transparency preserved" if document.has_transparency else "Opaque image",
            "Orientation corrected" if document.orientation_corrected else "Orientation unchanged",
        ]
        if document.frame_count > 1:
            lines.append(f"Frame/page: 1 of {document.frame_count}")
        self.details.set("\n".join(lines))
        self.color_note.set("\n\n".join((document.color_handling, *document.notes)))
        self.preview_info.set(f"{working_width:,} × {working_height:,} px  ·  sRGB  ·  Preview fitted to window")
        self.status.set("Image ready. Reduced to a 2,048 px longest side." if document.resized else "Image ready. Original dimensions retained.")
        self._draw_preview()

    def _schedule_preview(self, _event=None) -> None:
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(75, self._draw_preview)

    def _draw_preview(self) -> None:
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 10 or height < 10:
            return
        self.canvas.delete("all")
        if self.document is None:
            self.canvas.create_text(width / 2, height / 2 - 16, text="Drop your image here", fill="#334155", font=("Segoe UI", 19, "bold"))
            self.canvas.create_text(width / 2, height / 2 + 20, text="or click to choose a file", fill="#64748b", font=("Segoe UI", 11))
            return
        preview = make_preview(self.document.image, (max(1, width - 32), max(1, height - 32)), self.background.get() == "White paper")
        self._photo = ImageTk.PhotoImage(preview, master=self.root)
        self.canvas.create_image(width / 2, height / 2, image=self._photo)

    def close(self) -> None:
        self._closed = True
        for job in (self._poll_job, self._resize_job):
            if job is not None:
                self.root.after_cancel(job)
        self.root.destroy()


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        from tkinterdnd2 import TkinterDnD
    except ImportError:
        raise SystemExit("Install the dependencies first: python -m pip install -r requirements.txt")
    try:
        root = TkinterDnD.Tk()
    except tk.TclError as exc:
        raise SystemExit(f"A desktop window could not open. Check your Tk installation and display. Details: {exc}") from exc
    except RuntimeError as exc:
        raise SystemExit(
            "The drag-and-drop component could not start. Reinstall tkinterdnd2 in your "
            f"Python environment. Details: {exc}"
        ) from exc
    RotoDrawApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
