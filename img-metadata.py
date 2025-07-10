import sys
import os
import json
from PIL import Image, ExifTags, ImageCms, ImageStat
import imagehash
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from io import BytesIO

app = FastAPI(title="Image Metadata Extractor")

def get_gps_info(exif_data):
    gps_info = exif_data.get('GPSInfo')
    if not gps_info:
        return None
    def convert_to_degrees(value):
        d, m, s = value
        return d[0]/d[1] + m[0]/m[1]/60 + s[0]/s[1]/3600
    lat = convert_to_degrees(gps_info[2])
    if gps_info[1] == 'S':
        lat = -lat
    lon = convert_to_degrees(gps_info[4])
    if gps_info[3] == 'W':
        lon = -lon
    return {
        "latitude": lat,
        "longitude": lon,
        "google_maps": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    }

def extract_metadata(image_file):
    metadata = {}
    image = Image.open(image_file)
    metadata['format'] = image.format
    metadata['mode'] = image.mode
    metadata['size'] = image.size
    metadata['filename'] = getattr(image_file, 'name', 'unknown')
    metadata['file_size_bytes'] = os.fstat(image_file.fileno()).st_size if hasattr(image_file, 'fileno') else None

    icc_profile = image.info.get('icc_profile')
    if icc_profile:
        try:
            profile = ImageCms.getOpenProfile(BytesIO(icc_profile))
            metadata['icc_profile'] = profile.profile.product_desc.decode('utf-8', errors='ignore')
        except:
            metadata['icc_profile'] = "Embedded ICC profile found, could not parse description"
    else:
        metadata['icc_profile'] = None

    exif_data = {}
    exif_summary = {}
    gps_data = {}
    try:
        exif_raw = image._getexif()
        if exif_raw:
            for tag, value in exif_raw.items():
                decoded = ExifTags.TAGS.get(tag, tag)
                exif_data[decoded] = value
            gps_info = get_gps_info(exif_data)
            if gps_info:
                gps_data = gps_info
            # Extract summary EXIF fields
            exif_summary['camera_make'] = exif_data.get('Make')
            exif_summary['camera_model'] = exif_data.get('Model')
            exif_summary['iso'] = exif_data.get('ISOSpeedRatings')
            exif_summary['exposure_time'] = exif_data.get('ExposureTime')
            exif_summary['aperture'] = exif_data.get('FNumber')
            exif_summary['focal_length'] = exif_data.get('FocalLength')
            exif_summary['date_taken'] = exif_data.get('DateTimeOriginal')
    except:
        pass
    metadata['exif'] = exif_summary
    metadata['gps'] = gps_data

    try:
        metadata['perceptual_hash'] = str(imagehash.phash(image))
    except:
        metadata['perceptual_hash'] = None

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
        print("Usage: python extract_metadata.py <image_path>")
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
