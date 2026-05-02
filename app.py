"""Flask app — Commons Image Prep."""

from flask import Flask, render_template, request, jsonify, send_file
from io import BytesIO

from converter import (
    convert_image, commons_check, read_exif, open_image,
    HEIF_OK, COMMONS_ACCEPTED, COMMONS_REJECTED,
)
from filename import suggest_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB cap


@app.route("/")
def index():
    return render_template("index.html", heif_ok=HEIF_OK)


# ─── INSPECT (no conversion, just analysis) ────────────────────────

@app.route("/api/inspect", methods=["POST"])
def api_inspect():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file uploaded"}), 400

    blob = f.read()
    if not blob:
        return jsonify({"error": "Empty file"}), 400

    check = commons_check(f.filename, len(blob))
    name_check = suggest_filename(f.filename)

    info: dict = {
        "filename":     f.filename,
        "size_bytes":   len(blob),
        "commons":      check,
        "filename_check": name_check,
        "exif":         {"present": False, "tags": {}, "sensitive": []},
        "image": None,
    }

    # Try opening to extract dims + exif (only for raster images)
    try:
        img = open_image(blob)
        info["image"] = {
            "width":  img.size[0],
            "height": img.size[1],
            "mode":   img.mode,
            "format": img.format,
        }
        info["exif"] = read_exif(img)
    except Exception as e:
        info["image_error"] = str(e)

    return jsonify(info)


# ─── CONVERT ───────────────────────────────────────────────────────

@app.route("/api/convert", methods=["POST"])
def api_convert():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file uploaded"}), 400

    blob = f.read()
    if not blob:
        return jsonify({"error": "Empty file"}), 400

    target  = request.form.get("format", "jpg")
    quality = int(request.form.get("quality", 90))
    exif    = request.form.get("exif", "keep")  # keep | gps | all
    max_dim = request.form.get("max_dim")
    max_dim_int = int(max_dim) if max_dim and max_dim.isdigit() else None

    try:
        result = convert_image(
            blob, f.filename,
            target_format=target,
            quality=quality,
            max_dim=max_dim_int,
            exif_mode=exif,
        )
    except Exception as e:
        return jsonify({"error": f"Conversion failed: {e}"}), 400

    # Return as downloadable file
    return send_file(
        BytesIO(result["bytes"]),
        mimetype=result["mime"],
        as_attachment=True,
        download_name=result["suggested_filename"],
    )


# ─── FILENAME SUGGESTION ───────────────────────────────────────────

@app.route("/api/filename", methods=["POST"])
def api_filename():
    body = request.get_json(silent=True) or {}
    name = (body.get("filename") or "").strip()
    hint = (body.get("hint") or "").strip()
    if not name:
        return jsonify({"error": "Filename required"}), 400
    return jsonify(suggest_filename(name, hint))


@app.errorhandler(413)
def too_big(_e):
    return jsonify({"error": "File too large (50 MB max)"}), 413


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5004)