import time
import os
import shutil
import requests
import json
from flask import Flask, render_template, request, jsonify, session, send_file
from werkzeug.utils import secure_filename
from flask_http_middleware import MiddlewareManager
from middleware import AccessMiddleware, MetricsMiddleware, SecureRoutersMiddleware
from marshmallow import Schema, fields, ValidationError
from datetime import datetime

app = Flask(__name__)

app.wsgi_app = MiddlewareManager(app)

app.wsgi_app.add_middleware(AccessMiddleware)
app.wsgi_app.add_middleware(MetricsMiddleware)
        
# Use the TS_WEB_SECRET_KEY environment variable as the secret key, and the fallback
app.secret_key = os.environ.get('TS_WEB_SECRET_KEY', 'some_secret_key')

TRANSCRIBED_FOLDER = '/transcriptionstream/transcribed'
UPLOAD_FOLDER = '/transcriptionstream/incoming'
ALLOWED_EXTENSIONS = set(['mp3', 'wav', 'ogg', 'flac'])
MIME_TYPES = dict({
    "audio/mpeg": "mp3",
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

class AudioSchema(Schema):
    fileUrl = fields.String(required=True)
    audioId = fields.Integer(required=True)
    rowId = fields.Integer(required=True)

@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    request_data = request.json
    schema = AudioSchema()
    try:
        result = schema.load(request_data)
    except ValidationError as err:
        return jsonify(success=False, errors=err.messages), 400

    headers = {'Authorization': "Bearer " + os.environ.get('SALESDOCK_AUTHORIZATION')}
    response = requests.get(request_data['fileUrl'], headers=headers, verify=False)
    if response.status_code == requests.codes.ok:
        contentType = response.headers.get('content-type')
        extension = get_extension(contentType)
        if (extension == False):
            raise Exception('Invalid extension')

        filename = secure_filename(str(request_data['rowId']) + '-' + str(request_data['audioId']) + '.' + extension)
        with open(os.path.join(app.config['UPLOAD_FOLDER'], 'diarize', filename), mode="wb") as file:
            file.write(response.content)

        filenameJson = secure_filename(str(request_data['rowId']) + '-' + str(request_data['audioId']) + '.json')
        with open(os.path.join(app.config['UPLOAD_FOLDER'], 'data', filenameJson), mode="w") as file:
            json.dump(request_data, file)
    else:
        return jsonify(success=False, error=file.text), 400
    return jsonify(success=True, message="File saved successfully"), 200

@app.route('/load_files', methods=['POST'])
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


@app.route('/get_file/<path:folder>/<path:filename>', methods=['GET'])
def get_file(folder, filename):
    folder_path = os.path.join(TRANSCRIBED_FOLDER, folder)
    file_path = os.path.join(folder_path, filename)
    return send_file(file_path, as_attachment=True, download_name=filename)


@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        url = 'http://salesdock.nl'
        response = requests.get(url)
        if response.status_code == 200:
            with open(os.path.join(TRANSCRIBED_FOLDER, 'some'), "wb") as file:
                file.write(response.content)
                print("File downloaded successfully!")
        else:
            print("Failed to download the file.")
        # file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'ssss.js'))
        return render_template('upload.html', message="File uploaded successfully! Redirecting - you will be notified once the transcription is complete", redirect=True)
    return render_template('upload.html')
    
@app.route('/upload_transcribe', methods=['POST'])
def upload_transcribe():
    if 'file' not in request.files:
        raise Exception('No file')
    file = request.files['file']
    if file.filename == '':
        raise Exception('No file')
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'transcribe', filename))
        return jsonify(message="File uploaded successfully to Transcribe!")

@app.route('/upload_diarize', methods=['POST'])
def upload_diarize():
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'diarize', filename))
        return render_template('upload.html', message="File uploaded successfully to Diarize!")


@app.route('/check_alert', methods=['GET'])
def check_alert():
    all_folders = [os.path.join(TRANSCRIBED_FOLDER, f) for f in os.listdir(TRANSCRIBED_FOLDER) if
                   os.path.isdir(os.path.join(TRANSCRIBED_FOLDER, f))]

    alert_data = []
    for folder_path in all_folders:
        folder_name = os.path.basename(folder_path)
        folder_ctime = datetime.fromtimestamp(os.path.getctime(folder_path))

        if folder_ctime > session_start_time:
            # Define the list of possible audio file extensions
            audio_extensions = ['.mp3', '.wav', '.ogg', '.flac']

            # Check if the folder contains at least one audio file with any of the allowed extensions and one .srt file
            has_audio = any(file.endswith(tuple(audio_extensions)) for file in os.listdir(folder_path))
            has_srt = any(file.endswith('.srt') for file in os.listdir(folder_path))

            if has_audio and has_srt:
                alert_data.append({
                    'folder_name': folder_name,
                    'folder_time': folder_ctime.strftime('%Y-%m-%d %H:%M:%S')
                })

    return jsonify(alert=alert_data)


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
