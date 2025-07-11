import sys
import os
import json
from PIL import Image, ImageCms, ImageStat
import imagehash
import piexif
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from io import BytesIO

app = FastAPI(title="Image Metadata Extractor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_gps_info(gps_ifd):
    try:
        def convert_to_degrees(value):
            d, m, s = value
            return d[0]/d[1] + m[0]/m[1]/60 + s[0]/s[1]/3600

        lat = convert_to_degrees(gps_ifd[2])
        if gps_ifd[1] == b'S':
            lat = -lat
        lon = convert_to_degrees(gps_ifd[4])
        if gps_ifd[3] == b'W':
            lon = -lon

        alt = gps_ifd[6][0] / gps_ifd[6][1] if 6 in gps_ifd else None

        return {
            "latitude": lat,
            "longitude": lon,
            "altitude": alt,
            "google_maps": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        }
    except:
        return {}

def extract_metadata(image_file):
    metadata = {}
    image = Image.open(image_file)
    metadata['format'] = image.format
    metadata['mode'] = image.mode
    metadata['size'] = image.size
    metadata['filename'] = getattr(image_file, 'name', 'unknown')
    try:
        if hasattr(image_file, 'fileno'):
            metadata['file_size_bytes'] = os.fstat(image_file.fileno()).st_size
        else:
            # For BytesIO or streams without fileno
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
            metadata['icc_profile'] = profile.profile.product_desc.decode('utf-8', errors='ignore')
        except:
            metadata['icc_profile'] = "Embedded ICC profile found, could not parse description"
    else:
        metadata['icc_profile'] = None

    # EXIF Metadata
    exif_summary = {}
    gps_data = {}
    try:
        if "exif" in image.info:
            exif_dict = piexif.load(image.info["exif"])
            zeroth_ifd = exif_dict["0th"]
            exif_ifd = exif_dict["Exif"]
            gps_ifd = exif_dict["GPS"]

            exif_summary['camera_make'] = zeroth_ifd.get(piexif.ImageIFD.Make, b'').decode('utf-8', 'ignore')
            exif_summary['camera_model'] = zeroth_ifd.get(piexif.ImageIFD.Model, b'').decode('utf-8', 'ignore')
            exif_summary['iso'] = exif_ifd.get(piexif.ExifIFD.ISOSpeedRatings)
            exif_summary['exposure_time'] = exif_ifd.get(piexif.ExifIFD.ExposureTime)
            exif_summary['aperture'] = exif_ifd.get(piexif.ExifIFD.FNumber)
            exif_summary['focal_length'] = exif_ifd.get(piexif.ExifIFD.FocalLength)
            exif_summary['date_taken'] = exif_ifd.get(piexif.ExifIFD.DateTimeOriginal, b'').decode('utf-8', 'ignore')
            exif_summary['shutter_speed'] = exif_ifd.get(piexif.ExifIFD.ShutterSpeedValue)
            exif_summary['brightness_value'] = exif_ifd.get(piexif.ExifIFD.BrightnessValue)
            exif_summary['white_balance'] = exif_ifd.get(piexif.ExifIFD.WhiteBalance)
            exif_summary['metering_mode'] = exif_ifd.get(piexif.ExifIFD.MeteringMode)
            exif_summary['lens_model'] = exif_ifd.get(piexif.ExifIFD.LensModel, b'').decode('utf-8', 'ignore')
            exif_summary['exposure_program'] = exif_ifd.get(piexif.ExifIFD.ExposureProgram)
            exif_summary['software'] = zeroth_ifd.get(piexif.ImageIFD.Software, b'').decode('utf-8', 'ignore')
            exif_summary['orientation'] = zeroth_ifd.get(piexif.ImageIFD.Orientation)
            exif_summary['flash_fired'] = exif_ifd.get(piexif.ExifIFD.Flash)

            if gps_ifd:
                gps_data = get_gps_info(gps_ifd)
        else:
            exif_summary['info'] = "No EXIF data found in image."
    except Exception as e:
        exif_summary['error'] = str(e)

    metadata['exif'] = exif_summary
    metadata['gps'] = gps_data

    # Multi-hash for advanced duplicate/similarity detection
    try:
        metadata['perceptual_hash'] = str(imagehash.phash(image))
        metadata['average_hash'] = str(imagehash.average_hash(image))
        metadata['difference_hash'] = str(imagehash.dhash(image))
        metadata['wavelet_hash'] = str(imagehash.whash(image))
    except:
        metadata['hashes'] = "Unable to compute hashes"

    # Histogram Data (mean, median, stddev, rms, and full bins)
    try:
        stat = ImageStat.Stat(image.convert('RGB'))
        metadata['histogram_mean'] = stat.mean
        metadata['histogram_median'] = stat.median
        metadata['histogram_stddev'] = stat.stddev
        metadata['histogram_rms'] = stat.rms

        histogram_bins = image.convert('RGB').histogram()
        metadata['histogram_bins'] = histogram_bins  # List of 768 values (256 per R, G, B)
    except:
        metadata['histogram'] = "Unable to compute histogram"

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
