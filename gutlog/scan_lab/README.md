# scan_lab — how the scanner numbers were measured

Offline, no browser, no phone. `make_images.py` renders a lab report and
photographs it four ways (on a desk, filling the frame, held close so no desk
is visible at all, and harshly side-lit), keeping the text mask as ground
truth. `shim.js` is the smallest DOM that `scanner_widget.js` will run in, with
a real 2D canvas behind it, so the widget's own `autoDetect`, `borderVisible`
and `enhanceGray` can be driven directly. `run.js` puts an image through the
whole capture → detect → crop → warp → enhance path for either version, and
`measure_lib.py` scores the result against the mask: how much of the ink
survived the crop, paper-minus-ink in the lit and shadowed halves, the grain on
blank paper, and the page's resolution in the saved file.

    python3 make_images.py
    node run.js new harsh harsh_new
    python3 measure.py

`test_phase_h.py` (one directory up) runs all of it and asserts the thresholds.
The `.bin`, `.npy` and `.png` files it writes are working files and are not
committed.
