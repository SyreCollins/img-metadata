import sys
import os
import json
from PIL import Image, ImageCms, ImageStat
import imagehash
import piexif
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from io import BytesIO

app = FastAPI(title="Image Metadata Extractor")

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
    metadata['file_size_bytes'] = os.fstat(image_file.fileno()).st_size if hasattr(image_file, 'fileno') else None

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

            if gps_ifd:
                gps_data = get_gps_info(gps_ifd)
        else:
            exif_summary['info'] = "No EXIF data found in image."
    except Exception as e:
        exif_summary['error'] = str(e)

    metadata['exif'] = exif_summary
    metadata['gps'] = gps_data

    # Perceptual Hash
    try:
        metadata['perceptual_hash'] = str(imagehash.phash(image))
    except:
        metadata['perceptual_hash'] = None

    # Histogram Data
    try:
        stat = ImageStat.Stat(image.convert('RGB'))
        metadata['histogram_mean'] = stat.mean
        metadata['histogram_median'] = stat.median
        metadata['histogram_stddev'] = stat.stddev
    except:
        metadata['histogram'] = None

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
