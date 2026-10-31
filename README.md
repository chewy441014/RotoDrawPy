# RotoDraw: image setup

A local Python desktop application with file selection, file drag and drop,
an image preview, and the first image-processing stage: loading and normalization.

## Start on Windows

Install [Git for Windows](https://git-scm.com/install/windows) and
[Python 3.11 or newer](https://www.python.org/downloads/windows/), including
**Tcl/Tk and IDLE** and the Python launcher.

Open PowerShell in the folder where you want to keep the project and run:

```powershell
git clone https://github.com/chewy441014/RotoDrawPy.git
cd RotoDrawPy
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe rotodraw.py
```

After installation, double-click `launch_windows.bat` to open the app again.
Activation of the virtual environment is not required.

If you already installed the ZIP version, clone this repository into a separate
folder and create its virtual environment with the commands above. Your input
artwork and previously saved images can be opened from their existing locations.

## Get updates

Close RotoDraw. Open PowerShell inside your `RotoDrawPy` folder and run:

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then launch with `launch_windows.bat` or run
`.\.venv\Scripts\python.exe rotodraw.py` again. The `.venv` folder is local and
excluded from Git.

## Use

1. Choose an image, or drag one file from File Explorer into the preview.
2. Inspect its normalized preview and the source/working dimensions.
3. Choose a pencil-color limit from 1 to 30; the default is 30.
4. Switch between a transparency checkerboard and white-paper preview.
5. Use **Save normalized PNG** to save the working image as a new file.

`examples/flower.png` is a small test drawing with filled colored shapes,
black line details, a transparent exterior, and a transparent enclosed center.

## Implemented processing

- Decode PNG, JPEG, WebP, BMP, TIFF, or GIF by file contents.
- Correct EXIF orientation before calculating the working size.
- Convert a valid embedded ICC color profile to sRGB using LittleCMS.
- Preserve alpha, including indexed transparency, partial transparency, and
  transparent areas enclosed by opaque artwork.
- Convert the working image to 8-bit RGBA.
- Proportionally downscale to a maximum longest side of **2,048 pixels** using
  Lanczos resampling. Images already within this size retain their dimensions.
- Keep the source file unchanged. Preview backgrounds affect only the display.
- Load and save on a worker thread, keeping Tk calls on the main thread.
- Save a new PNG with an sRGB profile and `rotodraw.settings` text metadata.
- Write exports atomically so a failed write does not replace an existing export.

The output of `load_and_normalize` is a `NormalizedImage` record containing the
detached Pillow RGBA image, source and oriented dimensions, color-handling notes,
and the output profile. Later processing can consume its `image` directly.

## Color handling and input boundaries

Untagged RGB and grayscale values are assumed to be sRGB. Untagged CMYK uses
Pillow's approximate RGB conversion and is marked accordingly in the interface.
Invalid or incompatible embedded profiles are reported rather than ignored.
LAB input requires an embedded profile. High-bit-depth/integer/float image modes
are rejected with an instruction to export an 8-bit image.

Animated/multi-page inputs use their first frame/page; a note identifies this.
SVG and PDF must be exported to a supported raster image before importing.
Source images above 80 million pixels, or images rejected by Pillow's decoding
limits, must be resized externally before importing.

## Scope of this version

This version prepares the image. It does not remove backgrounds, detect the
subject's holes in an opaque image, extract a pencil palette, simplify gradients,
or generate stencil geometry/STLs. Existing transparent holes are preserved.

The requested pencil limit is saved for the later color-extraction stage. It
does not reduce the colors in the normalized image. No Prismacolor mapping has
been applied yet. The settings metadata contains no absolute source path.

## Files

- `rotodraw.py`: Tkinter interface, preview, drag and drop, and worker coordination.
- `image_loader.py`: independent loading, normalization, and PNG export functions.
- `requirements.txt`: pinned runtime dependencies.
- `launch_windows.bat`: Windows launcher.
- `tests/`: normalization and desktop integration checks using `unittest`.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Desktop tests briefly open windows. They are skipped when no graphical display
or drag-and-drop extension is available. They exercise file selection, the drop
callback and extension registration, save behavior, validation, and failed loads.
They do not automate a real drag gesture from Windows File Explorer.

Linux and macOS use the same Python entry point, but require a working Tk
installation. This project's primary target is Windows.

## API documentation used

- [Pillow ImageOps](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html)
- [Pillow ImageCms](https://pillow.readthedocs.io/en/stable/reference/ImageCms.html)
- [Pillow Image](https://pillow.readthedocs.io/en/stable/reference/Image.html)
- [TkinterDnD2](https://pypi.org/project/tkinterdnd2/)

## Validation of this build

22 image-loading and input checks passed. Desktop integration tests are included
for running on your PC; the desktop interface and real Windows Explorer drag
gesture have not been verified here.
