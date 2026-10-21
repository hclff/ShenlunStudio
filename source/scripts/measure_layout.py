"""Compare vertical layout landmarks between two captures of the same page.

Eyeballing screenshots taken at different device pixel ratios invents deltas that
do not exist: a 150% capture resized to 100% shifts text baselines by a few
pixels, and reading that as "the table got shorter" wastes a release cycle. This
tool removes the guesswork -- both images are resized to the same geometry and
the colour transitions along one column are printed, so widget edges and row
pitches can be compared in logical pixels.

    python scripts/measure_layout.py --page 2 --column 250
"""
import argparse
from pathlib import Path

from PIL import Image

REFERENCE = Path('e:/vibework/ShenlunStudio/ShenlunStudio-0.9.5-Project'
                 '/validation/frozen-final-20261021-015130')
CANDIDATE = Path('e:/vibework/ShenlunStudio/release_1.0/source/dist/ShenlunStudio/界面预览')


def transitions(path, size, column, top, bottom, threshold):
    image = Image.open(path).convert('RGB').resize(size, Image.LANCZOS)
    pixels = image.load()
    found = []
    previous = pixels[column, top]
    for y in range(top + 1, bottom):
        current = pixels[column, y]
        if max(abs(current[i] - previous[i]) for i in range(3)) > threshold:
            found.append((y, previous, current))
        previous = current
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page', type=int, default=2, help='workspace page 1-6')
    parser.add_argument('--column', type=int, default=250, help='x pixel to scan')
    parser.add_argument('--top', type=int, default=101)
    parser.add_argument('--bottom', type=int, default=620)
    parser.add_argument('--threshold', type=int, default=6, help='colour step that counts as an edge')
    parser.add_argument('--reference-dir', type=Path, default=REFERENCE)
    parser.add_argument('--candidate-dir', type=Path, default=CANDIDATE)
    args = parser.parse_args(argv)

    reference_path = args.reference_dir / f'workbench-pages-{args.page}.png'
    candidate_path = args.candidate_dir / f'{args.page:02d}-工作区.png'
    for path in (reference_path, candidate_path):
        if not path.is_file():
            print('missing image: %s' % path)
            return 1

    size = Image.open(reference_path).size
    print('geometry %dx%d, column %d' % (size[0], size[1], args.column))
    for name, path in (('0.9.5', reference_path), ('current', candidate_path)):
        print('--- %s' % name)
        for y, before, after in transitions(path, size, args.column, args.top, args.bottom, args.threshold):
            print('  y=%3d  %s -> %s' % (y, before, after))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
