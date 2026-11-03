"""Compares the add-on's QR encoder with the reference 'qrcode' library (pip install qrcode) module for module: every
version 1-10 with every mask, byte mode at level M, plus the automatic choice and the PNG container."""
import os
import struct
import sys
import zlib

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import qrcode  # noqa: E402
except ImportError:
    print('the reference library is not installed: pip install qrcode')
    sys.exit(2)
from qrcode.util import MODE_8BIT_BYTE, QRData  # noqa: E402
from resources.lib import qr  # noqa: E402

CAPACITY_M = {1: 14, 2: 26, 3: 42, 4: 62, 5: 84, 6: 106, 7: 122, 8: 152, 9: 180, 10: 213}
failures = 0
compared = 0
for version, capacity in CAPACITY_M.items():
    for length in (1, capacity // 2, capacity):
        text = ('https://simkl.com/pin?user_code=AB12-CD34 ' * 10)[:length]
        for mask in range(8):
            reference = qrcode.QRCode(version=version, error_correction=qrcode.constants.ERROR_CORRECT_M, mask_pattern=mask, border=0)
            reference.add_data(QRData(text.encode('utf-8'), mode=MODE_8BIT_BYTE))
            reference.make(fit=False)
            expected = [[bool(cell) for cell in row] for row in reference.get_matrix()]
            got = qr.matrix(text, version=version, mask=mask)
            compared += 1
            if got != expected:
                failures += 1
                print('MISMATCH version %d mask %d length %d' % (version, mask, length))

url = 'https://simkl.com/pin?user_code=AB12-CD34'
auto = qr.matrix(url)
reference = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
reference.add_data(QRData(url.encode('utf-8'), mode=MODE_8BIT_BYTE))
reference.make(fit=True)
print('auto: ours version %d (%d modules), reference version %d; same mask choice: %s' % (
    (len(auto) - 17) // 4, len(auto), reference.version, auto == [[bool(cell) for cell in row] for row in reference.get_matrix()]))
if any(auto == qr.matrix(url, mask=m) for m in range(8)) is False:
    failures += 1
    print('MISMATCH: the automatic matrix is none of the eight masked ones')

image = qr.png(url)
size = (len(auto) + 8) * 6
header = image[:8] == b'\x89PNG\r\n\x1a\n'
width, height, depth, colour = struct.unpack('>IIBB', image[16:26])
idat_length = struct.unpack('>I', image[33:37])[0]
pixels = zlib.decompress(image[41:41 + idat_length])
rows = [pixels[i * (size + 1) + 1:(i + 1) * (size + 1)] for i in range(size)]
quiet_ok = all(b == 255 for b in rows[0]) and all(b == 255 for b in rows[-1]) and all(row[0] == 255 and row[-1] == 255 for row in rows)
centre = rows[(4 + 3) * 6 + 3][(4 + 3) * 6 + 3]
print('png: signature %s, %dx%d depth %d colour %d (expected %d), quiet zone white %s, finder centre black %s' % (
    header, width, height, depth, colour, size, quiet_ok, centre == 0))
if not (header and width == height == size and depth == 8 and colour == 0 and quiet_ok and centre == 0 and len(pixels) == size * (size + 1)):
    failures += 1

print('%d matrices compared, %d failure(s)' % (compared, failures))
sys.exit(1 if failures else 0)
