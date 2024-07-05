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
ollamaIp = os.environ.get('OLLAMA_ENDPOINT_IP', '172.30.1.3')
salesdockUrl = os.environ.get('SALESDOCK_URL', 'https://app.salesdock.nl')

TRANSCRIBED_FOLDER = '/transcriptionstream/transcribed'
UPLOAD_FOLDER = '/transcriptionstream/incoming'
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
    id = fields.Integer(required=True)
    text = fields.Str(required=True)
    class Meta:
        strict = True

class AudioSchema(Schema):
    audioId = fields.Integer(required=True)
    url = fields.Str(required=True)
    class Meta:
        strict = True

class AudioAnalysisSchema(Schema):
    rowId = fields.Integer(required=True)
    audios = fields.Nested(AudioSchema, required=True, validate=validate.Length(min=1, error='Field may not be an empty list'), many=True)
    checkPoints = fields.Nested(CheckPointsSchema, required=True, validate=validate.Length(min=1, error='Field may not be an empty list'), many=True)
    returnHook = fields.Str(required=True)

class GenerateSchema(Schema):
    prompt = fields.Str(required=True)
    model = fields.Str(required=True)
    options = fields.Dict()

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

@app.route('/upload', methods=['POST'])
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
        audioUrl = salesdockUrl + '/' + audio['url']
        response = requests.get(audioUrl, headers=headers, verify=False)
        if response.status_code == requests.codes.ok:
            contentType = response.headers.get('content-type')
            extension = get_extension(contentType)
            if (extension == False):
                raise Exception('Invalid extension for a file')

            filename = secure_filename(str(audio['audioId']) + '.' + extension)
            with open(os.path.join(folderPath, filename), mode="wb") as file:
                file.write(response.content)
        else:
            raise Exception('Download from url failed for ' + audioUrl)

    with open(os.path.join(folderPath, secure_filename('data.json')), mode="w") as file:
        json.dump(request_data, file)
    return jsonify(success=True, message="File saved successfully"), 200

@app.route('/summary/<path:folder>', methods=['GET'])
def summary(folder):
    folderPath = os.path.join(TRANSCRIBED_FOLDER, folder)
    if not os.path.exists(folderPath):
        return jsonify(error='Folder does not exist'), 404

    summary = False
    if os.path.isfile(os.path.join(folderPath, 'summary.json')):
        with open(os.path.join(folderPath, 'summary.json')) as f:
            summary = json.load(f)

    audios = []
    for dir in os.listdir(folderPath):
        subDir = os.path.join(folderPath, dir)
        if os.path.isdir(subDir):
            transcriptionFile = os.path.join(subDir, dir + '.txt')
            transcriptionFileContents = False
            if os.path.isfile(transcriptionFile):
                with open(transcriptionFile) as f:
                    transcriptionFileContents = f.read()

            transcriptionJson = os.path.join(subDir, dir + '.json')
            transcriptionJsonContents = False
            if os.path.isfile(transcriptionJson):
                with open(transcriptionJson, encoding='utf-8-sig') as f:
                    transcriptionJsonContents = json.load(f)

            audios.append({'id': dir, 'text': transcriptionFileContents, 'json': transcriptionJsonContents})

#             transcriptionJson = os.path.join(subDir, dir + '.json')
#             if os.path.isfile(transcriptionJson):
#                 with open(transcriptionJson) as f:
#                     transcription = json.load(f)
#                     audios.append(transcription)

    return jsonify(success=True, summary=summary, audios=audios)

@app.route('/generate', methods=['POST'])
def generate():
    request_data = request.json
    schema = GenerateSchema()
    try:
        result = schema.load(request_data)
    except ValidationError as err:
        return jsonify(success=False, errors=err.messages), 400

    ollamaUrl = 'http://' + ollamaIp + ':11434'
    payload = {
        "model": request_data["model"],
        "prompt": request_data['prompt'],
        "stream": False,
        "keep_alive": "5s",
        "format": "json",
        "options" : request_data['options']
    }

    apiResponse = requests.get(ollamaUrl, timeout=5)
    if apiResponse.status_code != 200 or apiResponse.text != "Ollama is running":
        raise Exception('Api is not working')

    requestUrl = ollamaUrl + '/api/generate'
    response = None
    try:
        response = requests.post(requestUrl, json=payload)
    except Exception as e:
        raise Exception("Error sending request to API endpoint: {}".format(e))

    json_data = response.json()

    if response is not None and response.status_code == 200:
        json_data = response.json()
        return jsonify(success=True, data=json_data), 200

    return jsonify(success=False, message=response.error), 200

@app.route('/delete/<path:folder>', methods=['DELETE'])
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
