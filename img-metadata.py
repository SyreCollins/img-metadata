import sys
import os
import json
from PIL import Image, ImageCms, ImageStat
import imagehash
import exifread
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from io import BytesIO
from collections import Counter

app = FastAPI(title="Image Metadata Extractor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def extract_exif_with_exifread(image_file):
    image_file.seek(0)
    tags = exifread.process_file(image_file, details=False)
    exif_data = {}
    gps_data = {}
    for tag in tags.keys():
        clean_tag = str(tag)
        if clean_tag.startswith("GPS"):
            gps_data[clean_tag] = str(tags[tag])
        else:
            exif_data[clean_tag] = str(tags[tag])
    return exif_data, gps_data

def extract_dominant_colors(image, num_colors=5):
    small_image = image.resize((100, 100))
    pixels = list(small_image.getdata())
    counter = Counter(pixels)
    most_common = counter.most_common(num_colors)
    color_info = []
    for color, count in most_common:
        hex_color = '#%02x%02x%02x' % color if isinstance(color, tuple) else str(color)
        color_info.append({
            "color": hex_color,
            "count": count
        })
    return color_info

def calculate_aspect_ratio_and_mp(size):
    width, height = size
    def gcd(a, b):
        return a if b == 0 else gcd(b, a % b)
    divisor = gcd(width, height)
    aspect_ratio = f"{width // divisor}:{height // divisor}"
    megapixels = round((width * height) / 1_000_000, 2)
    return aspect_ratio, megapixels

def extract_metadata(image_file):
    metadata = {}
    image = Image.open(image_file)
    metadata['format'] = image.format
    metadata['mode'] = image.mode
    metadata['size'] = image.size
    metadata['filename'] = getattr(image_file, 'name', 'unknown')

    # File size detection
    try:
        if hasattr(image_file, 'fileno'):
            metadata['file_size_bytes'] = os.fstat(image_file.fileno()).st_size
        else:
            current_pos = image_file.tell()
            image_file.seek(0, os.SEEK_END)
            size = image_file.tell()
            image_file.seek(current_pos, os.SEEK_SET)
            metadata['file_size_bytes'] = size
    except Exception:
        metadata['file_size_bytes'] = None

    # ICC Profile
    icc_profile = image.info.get('icc_profile')
    if icc_profile:
        try:
            profile = ImageCms.getOpenProfile(BytesIO(icc_profile))
            desc = profile.profile.product_desc.decode('utf-8', errors='ignore').strip()
            metadata['icc_profile'] = desc if desc else "ICC profile embedded but no description provided."
        except:
            metadata['icc_profile'] = "Embedded ICC profile detected but unreadable. Likely sRGB or camera-specific."
    else:
        metadata['icc_profile'] = "No ICC profile embedded."

    # Aspect ratio and megapixels
    aspect_ratio, megapixels = calculate_aspect_ratio_and_mp(image.size)
    metadata['aspect_ratio'] = aspect_ratio
    metadata['megapixels'] = megapixels

    # Dominant Colors
    try:
        metadata['dominant_colors'] = extract_dominant_colors(image, num_colors=5)
    except:
        metadata['dominant_colors'] = "Unable to extract dominant colors."

    # EXIF and GPS via exifread
    try:
        image_file.seek(0)
        exif_data, gps_data = extract_exif_with_exifread(image_file)
        metadata['exif'] = exif_data if exif_data else "No EXIF data found."
        metadata['gps'] = gps_data if gps_data else "No GPS data found."
    except Exception as e:
        metadata['exif'] = f"Error extracting EXIF: {str(e)}"
        metadata['gps'] = "No GPS data found."

    # Hashes
    try:
        metadata['perceptual_hash'] = str(imagehash.phash(image))
        metadata['average_hash'] = str(imagehash.average_hash(image))
        metadata['difference_hash'] = str(imagehash.dhash(image))
        metadata['wavelet_hash'] = str(imagehash.whash(image))
    except:
        metadata['hashes'] = "Unable to compute hashes."

    # Histogram and RMS
    try:
        stat = ImageStat.Stat(image.convert('RGB'))
        metadata['histogram_mean'] = stat.mean
        metadata['histogram_median'] = stat.median
        metadata['histogram_stddev'] = stat.stddev
        metadata['histogram_rms'] = stat.rms
        histogram_bins = image.convert('RGB').histogram()
        metadata['histogram_bins'] = histogram_bins
    except:
        metadata['histogram'] = "Unable to compute histogram."

    return metadata

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python img-metadata.py <image_path>")
        sys.exit(1)
    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"File {image_path} does not exist.")
        sys.exit(1)
    with open(image_path, 'rb') as f:
        metadata = extract_metadata(f)
    print(json.dumps(metadata, indent=4))

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".tiff")):
        raise HTTPException(status_code=400, detail="Unsupported file type.")
    contents = await file.read()
    metadata = extract_metadata(BytesIO(contents))
    return JSONResponse(content=metadata)
