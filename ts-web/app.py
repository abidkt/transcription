import time
import os
import shutil
import requests
import json
from flask import Flask, render_template, request, jsonify, session, send_file
from werkzeug.utils import secure_filename
from flask_http_middleware import MiddlewareManager
from middleware import AccessMiddleware, MetricsMiddleware, SecureRoutersMiddleware
from marshmallow import Schema, fields, validate, ValidationError
from datetime import datetime

app = Flask(__name__)

app.wsgi_app = MiddlewareManager(app)

app.wsgi_app.add_middleware(AccessMiddleware)
app.wsgi_app.add_middleware(MetricsMiddleware)
        
# Use the TS_WEB_SECRET_KEY environment variable as the secret key, and the fallback
app.secret_key = os.environ.get('TS_WEB_SECRET_KEY', 'some_secret_key')

TRANSCRIBED_FOLDER = 'transcriptionstream/transcribed'
UPLOAD_FOLDER = 'transcriptionstream/incoming'
ALLOWED_EXTENSIONS = set(['mp3', 'wav', 'ogg', 'flac'])
MIME_TYPES = dict({
    "audio/mpeg": "mp3",
    "binary/octet-stream": "mp3",
    "audio/wav": "wav"
})

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

session_start_time = datetime.now()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_extension(content_type):
    for key, val in MIME_TYPES.items():
        if key == content_type:
            return val
    return False

class CheckPointsSchema(Schema):
    text = fields.Str(required=True)
    class Meta:
        strict = True

class AudioSchema(Schema):
    audioId = fields.Integer(required=True)
    url = fields.Url(required=True)
    class Meta:
        strict = True

class AudioAnalysisSchema(Schema):
    rowId = fields.Integer(required=True)
    audios = fields.Nested(AudioSchema, required=True, validate=validate.Length(min=1, error='Field may not be an empty list'), many=True)
    checkPoints = fields.Nested(CheckPointsSchema, required=True, validate=validate.Length(min=1, error='Field may not be an empty list'), many=True)

@app.route('/')
def index():
    # Reset the session variable on page load
    session['alerted_folders'] = []
    session['session_start_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    folder_paths = [os.path.join(TRANSCRIBED_FOLDER, f) for f in os.listdir(TRANSCRIBED_FOLDER) if os.path.isdir(os.path.join(TRANSCRIBED_FOLDER, f))]
    
    # Filter folders to only include those containing an .srt file
    valid_folders = []
    for folder in folder_paths:
        files = os.listdir(folder)
        if any(file.endswith('.srt') for file in files):
            valid_folders.append(os.path.basename(folder))
    
    sorted_folders = sorted(valid_folders, key=lambda s: s.lower())  # Sorting by name in ascending order, case-insensitive
    return jsonify({"transcriptions": sorted_folders})

@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    request_data = request.json
    schema = AudioAnalysisSchema()
    try:
        result = schema.load(request_data)
    except ValidationError as err:
        return jsonify(success=False, errors=err.messages), 400

    headers = {'Authorization': "Bearer " + os.environ.get('SALESDOCK_AUTHORIZATION')}

    folderPath = os.path.join(app.config['UPLOAD_FOLDER'], 'diarize', str(request_data['rowId']))
    if (os.path.exists(folderPath)):
        shutil.rmtree(folderPath)

    os.mkdir(folderPath)
    for audio in request_data['audios']:
        response = requests.get(audio['url'], headers=headers)
        if response.status_code == requests.codes.ok:
            contentType = response.headers.get('content-type')
            extension = get_extension(contentType)
            if (extension == False):
                raise Exception('Invalid extension')

            filename = secure_filename(str(audio['audioId']) + '.' + extension)
            with open(os.path.join(folderPath, filename), mode="wb") as file:
                file.write(response.content)
        else:
            raise Exception('Download from url failed')

    with open(os.path.join(folderPath, secure_filename('data.json')), mode="w") as file:
        json.dump(request_data, file)
    return jsonify(success=True, message="File saved successfully"), 200

@app.route('/get-summary', methods=['POST'])
def load_files():
    folder = request.form.get('folder')
    if not folder:
        return jsonify(error='Folder not specified'), 400
    
    folder_path = os.path.join(TRANSCRIBED_FOLDER, folder)
    if not os.path.exists(folder_path):
        return jsonify(error='Folder does not exist'), 404
    
    files = [f for f in os.listdir(folder_path) if not f.startswith('.')]
    audio_file = next((f for f in files if f.lower().endswith(('.mp3', '.wav', '.ogg', '.flac'))), None)
    srt_file = next((f for f in files if f.lower().endswith('.srt')), None)
    
    return jsonify(audio_file=audio_file, srt_file=srt_file, files=files)

@app.route('/delete_folder/<path:folder>', methods=['DELETE'])
def delete_folder(folder):
    folder_path = os.path.join(TRANSCRIBED_FOLDER, folder)
    if not os.path.exists(folder_path):
        return jsonify(success=False, error='Folder does not exist'), 404
    
    try:
        shutil.rmtree(folder_path)
        return jsonify(success=True)
    except Exception as e:
        print(f"Error deleting folder: {e}")
        return jsonify(success=False, error='Failed to delete folder'), 500    
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
